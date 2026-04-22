import random
import bencoding as benc
import hashlib

def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Socket closed")
        data += chunk
    return data

def create_peer_id(prefix="-TR417-", hex_len=13):
    return prefix + "".join(random.choices("0123456789ABCDEF", k=hex_len))

def create_handshake(decoded_torrent, peer_id):
    protocol_length = (19).to_bytes(1, "big")
    protocol_string = b"BitTorrent protocol"
    reserved_bytes = b"\x00" * 8
    info_hash = benc.get_info_hash_raw(decoded_torrent)
    return protocol_length + protocol_string + reserved_bytes + info_hash + peer_id.encode("utf-8")

def validate_handshake(response, decoded_torrent):
    if len(response) != 68:
        raise ConnectionError("Incomplete handshake")

    if response[1:20] != b"BitTorrent protocol":
        raise ConnectionError("Invalid handshake protocol")

    expected_info_hash = benc.get_info_hash_raw(decoded_torrent)
    if response[28:48] != expected_info_hash:
        raise ConnectionError("Info hash mismatch")

def send_handshake(sock, decoded_torrent, peer_id):
    sock.sendall(create_handshake(decoded_torrent, peer_id))
    response = recv_exact(sock, 68)
    validate_handshake(response, decoded_torrent)
    return response

def receive_handshake(sock, decoded_torrent):
    plen = int.from_bytes(recv_exact(sock, 1), "big")
    pstr = recv_exact(sock, plen)
    reserved = recv_exact(sock, 8)
    info_hash = recv_exact(sock, 20)
    remote_peer_id = recv_exact(sock, 20)

    if pstr != b"BitTorrent protocol":
        raise ConnectionError("Invalid handshake protocol")

    expected_info_hash = benc.get_info_hash_raw(decoded_torrent)
    if info_hash != expected_info_hash:
        raise ConnectionError("Info hash mismatch")

    return pstr, reserved, info_hash, remote_peer_id

def receive_message(sock):
    msg_length = int.from_bytes(recv_exact(sock, 4), "big")
    if msg_length == 0:
        return 0, None, b""

    msg_id = int.from_bytes(recv_exact(sock, 1), "big")
    payload = recv_exact(sock, msg_length - 1)
    return msg_length, msg_id, payload

def receive_bitfield(sock):
    while True:
        _, msg_id, payload = receive_message(sock)

        if msg_id is None:
            continue

        if msg_id == 5:
            return payload

        return b""

def send_interested(sock):
    sock.sendall((1).to_bytes(4, "big") + (2).to_bytes(1, "big"))

def wait_for_unchoke(sock):
    while True:
        _, msg_id, payload = receive_message(sock)

        if msg_id is None:
            continue

        if msg_id == 1:
            return 1

        if msg_id == 6:
            return 6, payload

def get_piece_size(index, num_pieces, piece_length, total_length):
    if index < 0 or index >= num_pieces:
        return 0

    if index == num_pieces - 1:
        remainder = total_length % piece_length
        if remainder != 0:
            return remainder

    return piece_length

def is_piece_available(index, bitfield):
    byte_index = index // 8
    bit_index = index % 8

    if byte_index >= len(bitfield):
        return False

    return (bitfield[byte_index] & (1 << (7 - bit_index))) != 0

def create_bitfield(num_pieces):
    return bytearray(num_pieces // 8 + (0 if num_pieces % 8 == 0 else 1))

def set_piece_bit(bitfield, index):
    byte = bitfield[index // 8]
    byte = byte | (2 ** (7 - (index % 8)))
    bitfield[index // 8] = byte

def get_piece_bit(bitfield, index):
    byte = bitfield[index // 8]
    return byte & (2 ** (7 - (index % 8)))

def check_piece_hash(pieces, data, index):
    expected = pieces[index * 20:(index + 1) * 20]
    return hashlib.sha1(data).digest() == expected