# BitTorrent Client

A Python BitTorrent client for downloading files from peers and seeding them back to the network. The project also includes a local testing client for verifying seeding behavior directly on the same machine.

## How It Works

### `main.py`

`main.py` is the primary BitTorrent client. It connects to peers using the BitTorrent protocol, downloading pieces of the specified file and verifying each piece with the hashes stored in the torrent metadata. After all the pieces have been written to disk, the client seeds the completed file back to the network.

| Argument | Description |
|----------|-------------|
| `-p`, `--port` | Port to use. Must be between `6881` and `6889`. |
| `-f`, `--file` | Path to the `.torrent` file |
| `-c`, `--compact` | Enable or disable compact peer messages in the tracker request. Enabled by default|
| `-s`, `--seed_only` | when nonzero, skip downloading and go straight to seeding |

### `local_download.py`

`local_download.py` is a direct-peer downloader used to test the seeding functionality of `main.py`. 

Seeding files back after downloading is good practice in the BitTorrent ecosystem, and this tool makes it easy to verify that the seeding path works correctly end to end by connecting directly to a local seeder rather than going through the tracker.

## Usage

### main.py
```
python3 main.py -p <port_number> -f <file_name>.torrent
```

### Test seeding locally with `local_download.py`
Start the seeder in one terminal:
```bash
python3 main.py -p 6882 -f debian-13.4.0-arm64-netinst.iso -s 1
```

Or we can leave the main BitTorrent Client running after it finishes downloading the file.

In another terminal:
```bash
python3 local_download.py
```
You will see pieces being downloaded through the local connection.

Note: we need to ensure that the port used in main.py matches the port specified in SEEDER_PORT within local_download.py.

## Project Structure

```
BitTorrent-Client/
├── main.py             # main BitTorrent downloader and seeder
├── local_download.py   # local direct-peer downloader for testing the seeding functionality of main.py
├── protocol.py         # shared BitTorrent protocol helpers
├── bencoding.py        # torrent parsing and tracker helper functions
```