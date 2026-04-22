import os
import socket
import sys
import argparse
import random
import time
import threading
import hashlib
import bencoding as benc

from protocol import (
    create_bitfield,
    create_handshake,
    create_peer_id,
    get_piece_size,
    is_piece_available,
    receive_bitfield,
    receive_handshake,
    receive_message,
    send_handshake,
    send_interested,
    set_piece_bit,
    wait_for_unchoke,
    check_piece_hash
)

downloaded = set()
all_pieces = set()
peer_list = {}

def is_valid_request(index, begin, length):
    if index < 0 or index >= num_pieces:
        return False
    if begin < 0 or length <= 0:
        return False

    piece_size = get_piece_size(index, num_pieces, piece_length, total_length)
    if begin + length > piece_size:
        return False

    return True

def build_piece_response(index, begin, length):
    if index not in downloaded:
        return None

    if not is_valid_request(index, begin, length):
        return None

    with file_lock:
        with open(file_path, "rb") as file:
            file.seek(index * piece_length + begin)
            response_block = file.read(length)

    if len(response_block) != length:
        return None

    response_len = (9 + len(response_block)).to_bytes(4, "big")
    response_index = index.to_bytes(4, "big")
    response_begin = begin.to_bytes(4, "big")
    return response_len + (7).to_bytes(1, "big") + response_index + response_begin + response_block


def rebuild_local_state_from_file(piece_info):
    verified_downloaded = set()
    verified_bitfield = create_bitfield(num_pieces)

    if not os.path.exists(file_path):
        return verified_downloaded, verified_bitfield

    actual_size = os.path.getsize(file_path)
    if actual_size < total_length:
        return verified_downloaded, verified_bitfield

    with file_lock:
        with open(file_path, "rb") as file:
            for index in range(num_pieces):
                expected_len = get_piece_size(index, num_pieces, piece_length, total_length)
                piece_data = file.read(expected_len)

                if len(piece_data) != expected_len:
                    break

                if check_piece_hash(piece_info, piece_data, index):
                    verified_downloaded.add(index)
                    set_piece_bit(verified_bitfield, index)

    return verified_downloaded, verified_bitfield


def connect_to_peer(piece_info, ip, port, peer_id):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)

        try:
            s.connect((ip, port))
            send_handshake(s, decoded_torrent, peer_id)
        except (socket.error, ConnectionError):
            s.close()
            return

        bitfield = receive_bitfield(s)
        send_interested(s)

        unchoke_result = wait_for_unchoke(s)
        if unchoke_result != 1:
            if isinstance(unchoke_result, tuple) and unchoke_result[0] == 6:
                req_index = int.from_bytes(unchoke_result[1][0:4], "big")
                req_begin = int.from_bytes(unchoke_result[1][4:8], "big")
                req_length = int.from_bytes(unchoke_result[1][8:12], "big")
                packet = build_piece_response(req_index, req_begin, req_length)
                if packet is not None:
                    s.sendall(packet)
            else:
                s.close()
                return

        run_client(piece_info, s, bitfield)
    except (socket.timeout, socket.error):
        return


def handle_request_message(sock, payload):
    index = int.from_bytes(payload[0:4], "big")
    begin = int.from_bytes(payload[4:8], "big")
    length = int.from_bytes(payload[8:12], "big")

    packet = build_piece_response(index, begin, length)
    if packet is not None:
        sock.sendall(packet)


def download_piece(sock, index):
    sock.settimeout(5)

    piece_size = get_piece_size(index, num_pieces, piece_length, total_length)
    if piece_size <= 0:
        return b""

    num_blocks = (piece_size + block_length - 1) // block_length
    chunk_arr = [b""] * num_blocks

    for i in range(num_blocks):
        begin = block_length * i
        request_len = min(block_length, piece_size - begin)

        request_msg = (
            (13).to_bytes(4, "big")
            + (6).to_bytes(1, "big")
            + index.to_bytes(4, "big")
            + begin.to_bytes(4, "big")
            + request_len.to_bytes(4, "big")
        )

        try:
            sock.sendall(request_msg)
        except socket.error:
            return b""

    received = 0

    while received != num_blocks:
        try:
            msg_length, msg_id, payload = receive_message(sock)

            if msg_id is None:
                continue

            if msg_id == 7:
                received_index = int.from_bytes(payload[0:4], "big")
                received_offset = int.from_bytes(payload[4:8], "big")
                block_data = payload[8:]

                chunk_index = received_offset // block_length
                if (
                    received_index == index
                    and 0 <= chunk_index < num_blocks
                    and chunk_arr[chunk_index] == b""
                ):
                    chunk_arr[chunk_index] = block_data
                    received += 1
                elif received_index != index:
                    return b""

            elif msg_id == 4:
                continue

            elif msg_id == 6:
                handle_request_message(sock, payload)

            elif msg_id == 0:
                wait_for_unchoke(sock)
                send_interested(sock)

        except (socket.error, ValueError, ConnectionError) as e:
            raise e

    return chunk_arr


def dedicated_seeding(peer_id, port, file_path, piece_length):
    print("Starting Dedicated Seeding")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", port))
    except OSError as err:
        print(f"Binding Failure : {err}")
        sys.exit(1)

    s.listen(socket.SOMAXCONN)

    while True:
        peersock, _ = s.accept()
        try:
            receive_handshake(peersock, decoded_torrent)
            peersock.sendall(create_handshake(decoded_torrent, peer_id))

            bitfield_msg = (len(local_bitfield) + 1).to_bytes(4, "big") + (5).to_bytes(1, "big") + local_bitfield
            peersock.sendall(bitfield_msg)

            unchoke_msg = (1).to_bytes(4, "big") + (1).to_bytes(1, "big")
            peersock.sendall(unchoke_msg)

            thread = threading.Thread(
                target=handle_seeding,
                args=(peersock, file_path, piece_length),
                daemon=True,
            )
            thread.start()
        except (socket.error, ConnectionError):
            peersock.close()


def handle_seeding(sock, file_path, piece_length):
    try:
        while True:
            msg_length, msg_id, payload = receive_message(sock)

            if msg_id is None:
                continue

            if msg_id == 6:
                handle_request_message(sock, payload)
    except (socket.error, ConnectionError):
        sock.close()


def run_client(piece_info, sock, bitfield):
    if bitfield == b"":
        return

    while True:
        available_pieces = all_pieces - downloaded
        if not available_pieces:
            return

        index = random.choice(list(available_pieces))

        if not is_piece_available(index, bitfield):
            continue

        try:
            chunk = download_piece(sock, index)
        except Exception:
            return

        if chunk == b"" or chunk is None:
            continue

        data = b"".join(chunk)
        if check_piece_hash(piece_info, data, index):
            with file_lock:
                with open(file_path, "r+b") as file:
                    file.seek(index * piece_length)
                    file.write(data)

            with lock:
                downloaded.add(index)

            with bitfield_lock:
                set_piece_bit(local_bitfield, index)


def arg_parse():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", type=int, required=True, help="The port to be used for the BitTorrent")
    parser.add_argument("-f", "--file", type=str, required=True, help="File to be downloaded")
    parser.add_argument("-c", "--compact", type=int, required=False, help="Compact Message", default=1)
    parser.add_argument("-s","--seed_only",type=int,required=False,help="when nonzero, skip peer connections and go straight to seeding",default=0)
    
    args = parser.parse_args()

    if args.port < 6881 or args.port > 6889:
        parser.error("Invalid Port Value. Port must be between 6881 or 6889")
        raise SystemExit(1)

    file = benc.decode_torrent(args.file)
    return args.port, file, args.compact, args


def hash_downloaded(file_path, piece_length, file_lock):
    digest = hashlib.sha512()
    with file_lock:
        with open(file_path, "rb", buffering=0) as file:
            while True:
                chunk = file.read(piece_length)
                if not chunk:
                    break
                digest.update(chunk)

    print(f"File: {file_path}")
    print(f"Our SHA512 Hash:\n{digest.hexdigest()}")


if __name__ == "__main__":
    port, decoded_torrent, compact, args = arg_parse()

    start = time.time()

    lock = threading.Lock()
    file_lock = threading.Lock()
    threads = []
    bitfield_lock = threading.Lock()

    piece_length = benc.get_piece_length(decoded_torrent)
    total_length = benc.get_length(decoded_torrent)
    block_length = 16384
    file_path = benc.get_file_name(decoded_torrent)
    num_pieces = benc.get_number_pieces(decoded_torrent)
    indices = list(range(num_pieces))
    local_bitfield = create_bitfield(num_pieces)
    piece_info = decoded_torrent[b"info"][b"pieces"]

    random.shuffle(indices)
    for index in indices:
        all_pieces.add(index)

    my_id = create_peer_id("-TR417-", 13)

    if args.seed_only == 0:
        if not os.path.exists(file_path):
            with open(file_path, "wb") as f:
                f.truncate(total_length)

        peer_ip_port_tuple = benc.get_ip_port_tupple_from_tracker(decoded_torrent, my_id, port, compact)

        for peer_ip, peer_port in peer_ip_port_tuple:
            thread = threading.Thread(target=connect_to_peer, args=(piece_info, peer_ip, peer_port, my_id), daemon=False)
            threads.append(thread)
            thread.start()

        while len(downloaded) < num_pieces:
            time.sleep(2)
            print(
                f"Downloaded {len(downloaded)} out of {num_pieces} pieces. "
                f"Time Lapsed: {time.time() - start:.2f} seconds."
            )

            dead_threads = [thread for thread in threads if not thread.is_alive()]
            for thread in dead_threads:
                threads.remove(thread)

            if len(threads) < 15:
                peer_ip_port_tuple = benc.get_ip_port_tupple_from_tracker(decoded_torrent, my_id, port, compact)
                for ip, peer_port in peer_ip_port_tuple:
                    thread = threading.Thread(target=connect_to_peer, args=(piece_info, ip, peer_port, my_id), daemon=True,)
                    threads.append(thread)
                    thread.start()

        print(f"{file_path} finished downloading all {num_pieces} pieces!")
        print(f"Time Total Taken: {time.time() - start:.2f} seconds")
        hash_downloaded(file_path, piece_length, file_lock)
    else:
        downloaded, local_bitfield = rebuild_local_state_from_file()
        print(f"Verified {len(downloaded)} out of {num_pieces} pieces from disk for seeding.")

    dedicated_seeding(my_id, args.port, file_path, piece_length)
