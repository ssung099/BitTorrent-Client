import socket
import time
import threading
import hashlib
import bencoding as benc

from protocol import (
    create_peer_id,
    get_piece_size,
    is_piece_available,
    receive_message,
    receive_bitfield,
    send_handshake,
    send_interested,
    wait_for_unchoke,
    check_piece_hash,
)

TORRENT_FILE = "debian-13.4.0-arm64-netinst.iso.torrent"
SEEDER_IP = "127.0.0.1"
SEEDER_PORT = 6882
BLOCK_LENGTH = 16384
OUTPUT_PREFIX = "downloaded_"

decoded_torrent = benc.decode_torrent(TORRENT_FILE)
piece_length = benc.get_piece_length(decoded_torrent)
num_pieces = benc.get_number_pieces(decoded_torrent)
total_length = benc.get_length(decoded_torrent)
source_name = benc.get_file_name(decoded_torrent)
piece_info = decoded_torrent[b"info"][b"pieces"]

output_file = OUTPUT_PREFIX + source_name
start = time.time()

downloaded = 0
download_error = None
download_done = False
progress_lock = threading.Lock()


def download_piece(sock, index):
    size = get_piece_size(index, num_pieces, piece_length, total_length)
    num_blocks = (size + BLOCK_LENGTH - 1) // BLOCK_LENGTH
    blocks = [None] * num_blocks

    for block_num in range(num_blocks):
        begin = block_num * BLOCK_LENGTH
        length = min(BLOCK_LENGTH, size - begin)

        request = (
            (13).to_bytes(4, "big")
            + (6).to_bytes(1, "big")
            + index.to_bytes(4, "big")
            + begin.to_bytes(4, "big")
            + length.to_bytes(4, "big")
        )
        sock.sendall(request)

    received = 0
    while received < num_blocks:
        _, msg_id, payload = receive_message(sock)

        if msg_id is None:
            continue

        if msg_id != 7:
            continue

        recv_index = int.from_bytes(payload[0:4], "big")
        recv_begin = int.from_bytes(payload[4:8], "big")
        block = payload[8:]

        if recv_index != index:
            continue

        block_index = recv_begin // BLOCK_LENGTH
        if 0 <= block_index < num_blocks and blocks[block_index] is None:
            blocks[block_index] = block
            received += 1

    return b"".join(blocks)


def worker():
    global downloaded, download_error, download_done

    sock = None
    try:
        with open(output_file, "wb") as f:
            f.truncate(total_length)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect((SEEDER_IP, SEEDER_PORT))

        peer_id = create_peer_id("-TR2940-", 12)
        send_handshake(sock, decoded_torrent, peer_id)
        bitfield = receive_bitfield(sock)
        send_interested(sock)
        wait_for_unchoke(sock)

        with open(output_file, "r+b") as f:
            for index in range(num_pieces):
                if not is_piece_available(index, bitfield):
                    continue

                data = download_piece(sock, index)

                if len(data) != get_piece_size(index, num_pieces, piece_length, total_length):
                    raise ValueError(f"Wrong piece size for piece {index}")

                if not check_piece_hash(piece_info, data, index):
                    raise ValueError(f"Hash mismatch on piece {index}")

                f.seek(index * piece_length)
                f.write(data)

                with progress_lock:
                    downloaded += 1

    except Exception as e:
        download_error = e
    finally:
        if sock is not None:
            sock.close()
        download_done = True


if __name__ == "__main__":
    thread = threading.Thread(target=worker, daemon=False)
    thread.start()

    while True:
        time.sleep(2)

        with progress_lock:
            current_downloaded = downloaded

        print(
            f"Downloaded {current_downloaded} out of {num_pieces} pieces. "
            f"Time elapsed: {time.time() - start:.2f}s"
        )

        if download_done:
            break

    thread.join()

    if download_error is not None:
        raise download_error

    sha512 = hashlib.sha512()
    with open(output_file, "rb") as f:
        while True:
            chunk = f.read(piece_length)
            if not chunk:
                break
            sha512.update(chunk)

    print(f"\n{output_file} finished downloading!")
    print(f"Time Total Taken: {time.time() - start:.2f} seconds")
    print(f"SHA512: {sha512.hexdigest()}")
