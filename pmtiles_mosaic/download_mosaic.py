#!/usr/bin/env -S uv run 
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "mercantile",
#     "pmtiles",
#     "requests",
# ]
# ///

import json
import sqlite3

from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, urljoin, unquote

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from pmtiles.reader import Reader, MmapSource, all_tiles
from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid, Compression, TileType

# heavily copied from https://github.com/protomaps/PMTiles/blob/main/python/pmtiles/convert.py
# and https://github.com/mapbox/mbutil/blob/master/mbutil/util.py

class PmtilesArchiveWriter:
    def __init__(self, output_file):
        self.pmtiles_writer = None
        self.output_file = output_file

    def init(self):
        self.pmtiles_writer = Writer(open(self.output_file, 'wb'))

    def add_to_archive(self, zxy, tile_data):
        tile_id = zxy_to_tileid(zxy[0], zxy[1], zxy[2])
        self.pmtiles_writer.write_tile(tile_id, tile_data)

    def commit(self):
        pass

    def enhance_header(self, h):
        h['tile_compression'] = Compression(h['tile_compression'])
        h['tile_type'] = TileType(h['tile_type'])


    def finalize(self, metadata, header):
        self.enhance_header(header)
        self.pmtiles_writer.finalize(header, metadata)


class MbtilesArchiveWriter:
    def __init__(self, output_file):
        self.conn = None
        self.cursor = None
        self.output_file = output_file

    def init(self):
        self.conn = sqlite3.connect(self.output_file)
        self.cursor = self.conn.cursor()
        
        self.cursor.execute("""PRAGMA synchronous=0""")
        self.cursor.execute("""PRAGMA locking_mode=EXCLUSIVE""")
        self.cursor.execute("""PRAGMA journal_mode=DELETE""")

        self.cursor.execute("CREATE TABLE metadata (name text, value text);")
        self.cursor.execute(
            "CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);"
        )

    def add_to_archive(self, zxy, tile_data):
        flipped_y = (1 << zxy[0]) - 1 - zxy[2]
        self.cursor.execute(
            "INSERT INTO tiles VALUES(?,?,?,?)",
            (zxy[0], zxy[1], flipped_y, tile_data),
        )

    def commit(self):
        self.conn.commit()

    def add_metadata(self, metadata):
        json_metadata = {}
        for k, v in metadata.items():
            if k == "vector_layers":
                json_metadata["vector_layers"] = v
                continue
            elif k == "tilestats":
                json_metadata["tilestats"] = v
                continue
            elif not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)

            self.cursor.execute("INSERT INTO metadata VALUES(?,?)", (k, v))
    
        if len(json_metadata) > 0:
            self.cursor.execute(
                "INSERT INTO metadata VALUES(?,?)",
                ("json", json.dumps(json_metadata, ensure_ascii=False)),
            )


    def finalize_mbtiles(self):
        self.cursor.execute(
            "CREATE UNIQUE INDEX tile_index on tiles (zoom_level, tile_column, tile_row);"
        )
        self.conn.commit()
        self.cursor.execute("""ANALYZE;""")
    
        self.conn.close()

    def enhance_metadata(self, cm, ch):
    
        cm['maxzoom'] = ch['max_zoom']
        cm['minzoom'] = ch['min_zoom']
    
        max_lat = ch['max_lat_e7'] / 10000000 
        min_lat = ch['min_lat_e7'] / 10000000
        max_lon = ch['max_lon_e7'] / 10000000
        min_lon = ch['min_lon_e7'] / 10000000
        cm['bounds'] = f"{min_lon},{min_lat},{max_lon},{max_lat}"
    
        center_lat = ch['center_lat_e7'] / 10000000
        center_lon = ch['center_lon_e7'] / 10000000
        center_zoom = ch['center_zoom']
        cm['center'] = f"{center_lon},{center_lat},{center_zoom}"

    def finalize(self, metadata, header):
        self.enhance_metadata(metadata, header)
        self.add_metadata(metadata)
        self.finalize_mbtiles()

def get_filename_from_url(url):
    url_parsed = urlparse(url)
    return unquote(PurePosixPath(url_parsed.path).name)

class Merger:
    def __init__(self, mosaic_url, archive_type, output_file, request_timeout_secs, num_http_retries):
        self.session = self.get_session(num_http_retries)
        self.request_timeout_secs = request_timeout_secs

        self.output_file = output_file
        self.output_dir = output_file.parent
        self.tracker_file = self.output_dir / 'tracker.txt'
        self.mosaic_file = self.output_dir / get_filename_from_url(mosaic_url)

        self.archive_type = archive_type
        self.archive_writer = self.get_archive_writer()

        self.mosaic_url = mosaic_url
        self.mosaic_data = None
        self.mosaic_version = 0
        self.done_stages = set()

    def get_archive_writer(self):
        if self.archive_type == 'mbtiles':
            return MbtilesArchiveWriter(self.output_file)

        if self.archive_type == 'pmtiles':
            return PmtilesArchiveWriter(self.output_file)

        raise ValueError(f'Unsupported archive type: {self.archive_type}')

    def get_session(self, num_http_retries):
        session = requests.session()
        retries = num_http_retries
        retry = Retry(
            total=retries,
            read=retries,
            connect=retries,
        )
        session.mount('http://', HTTPAdapter(max_retries=retry))
        session.mount('https://', HTTPAdapter(max_retries=retry))
        return session
 
    def get_mosaic(self, mosaic_url):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.mosaic_file.exists():
            self.download_file(mosaic_url, self.mosaic_file)
    
        self.mosaic_data = json.loads(self.mosaic_file.read_text())


    def download_file(self, url, file):
        print(f'downloading {url} to {file}')
        resp = self.session.get(url, stream=True, timeout=self.request_timeout_secs)
        with open(file, 'wb') as f:
            for data in resp.iter_content(chunk_size=4096):
                f.write(data)
   
    def init_tracker(self):
        if not self.tracker_file.exists():
            self.tracker_file.write_text("")

    def populate_done_list(self):
        if not self.tracker_file.exists():
            return

        self.done_stages = set(self.tracker_file.read_text().strip().split('\n'))

    def mark_as_done(self, stage):
        with open(self.tracker_file, 'a') as f:
            f.write(stage)
            f.write('\n')
        self.done_stages.add(stage)

    def get_pmtiles_url(self, k):
        if self.mosaic_version == 0 and k.startswith('../'):
            k = k[3:]
        return urljoin(self.mosaic_url, k)

    def collect_header(self, items):
        ch = {}
        for item in items:
            h = item['header']
            if 'max_zoom' not in ch or ch['max_zoom'] < h['max_zoom']:
                ch['max_zoom'] = h['max_zoom']
            if 'min_zoom' not in ch or ch['min_zoom'] > h['min_zoom']:
                ch['min_zoom'] = h['min_zoom']
    
            if 'max_lat_e7' not in ch or ch['max_lat_e7'] < h['max_lat_e7']:
                ch['max_lat_e7'] = h['max_lat_e7']
            if 'min_lat_e7' not in ch or ch['min_lat_e7'] > h['min_lat_e7']:
                ch['min_lat_e7'] = h['min_lat_e7']
            if 'max_lon_e7' not in ch or ch['max_lon_e7'] < h['max_lon_e7']:
                ch['max_lon_e7'] = h['max_lon_e7']
            if 'min_lon_e7' not in ch or ch['min_lon_e7'] > h['min_lon_e7']:
                ch['min_lon_e7'] = h['min_lon_e7']
    
            ch['tile_compression'] = h['tile_compression']
            ch['tile_type'] = h['tile_type']
    
        ch['center_lat_e7'] = (ch['max_lat_e7'] + ch['min_lat_e7']) // 2
        ch['center_lon_e7'] = (ch['max_lon_e7'] + ch['min_lon_e7']) // 2
        ch['center_zoom'] = (ch['max_zoom'] + ch['min_zoom']) // 2
    
        return ch

    def get_metadata_and_header(self):
        if self.mosaic_version != 0:
            metadata = self.mosaic_data['metadata']
            header = self.mosaic_data['header']
        else:
            metadata = list(self.mosaic_data.values())[0]['metadata']
            header = self.collect_header(self.mosaic_data.values())
    
        return metadata, header

    def add_pmtiles(self, pmtiles_fname):

        print(f'adding {pmtiles_fname} to archive')
        with open(pmtiles_fname, "r+b") as f:
            source = MmapSource(f)
            reader = Reader(source)
    
            for zxy, tile_data in all_tiles(reader.get_bytes):
                self.archive_writer.add_to_archive(zxy, tile_data)

            self.archive_writer.commit()


    def process(self):
        self.get_mosaic(self.mosaic_url)
        if 'version' in self.mosaic_data:
            self.mosaic_version = self.mosaic_data['version']

        if self.output_file.exists() and not self.tracker_file.exists():
            raise Exception(f'Output file {self.archive_writer.output_file} already exists, and no tracker file found. Exiting to avoid overwriting.')

        self.init_tracker()
        self.populate_done_list()

        self.archive_writer.init()

        slice_data = self.mosaic_data if self.mosaic_version == 0 else self.mosaic_data.get('slices', {})

        for k in slice_data.keys():

            pmtiles_url = self.get_pmtiles_url(k)
            if k in self.done_stages:
                print(f'Stage {k} already done, skipping')
                continue

            pmtiles_file = self.output_dir / get_filename_from_url(pmtiles_url)
            if pmtiles_file.exists():
                raise Exception(f'{pmtiles_file} already exists, delete existing file to continue')

            self.download_file(pmtiles_url, pmtiles_file)
            
            self.add_pmtiles(pmtiles_file)

            self.mark_as_done(k)

            pmtiles_file.unlink()

        metadata, header = self.get_metadata_and_header()
        self.archive_writer.finalize(metadata, header)

    def cleanup(self):
        if self.tracker_file.exists():
            self.tracker_file.unlink()
            print('Tracker file deleted.')

        if self.mosaic_file.exists():
            self.mosaic_file.unlink()
            print('Mosaic file deleted.')

def cli():
    import argparse
    parser = argparse.ArgumentParser(description='Download a mosaic and convert it to MBTiles or PMTiles format.')
    parser.add_argument('--mosaic-url',  '-u', required=True, type=str, help='URL of the mosaic JSON file')
    parser.add_argument('--output-file', '-o', type=str, help='Output MBTiles/PMTiles file name. The format is inferred from the extension.')
    parser.add_argument('--archive-type', '-a', choices=['mbtiles', 'pmtiles'], help='Type of archive to create. Required if --output-file is not provided.')
    parser.add_argument('--request-timeout-secs', '-t', type=int, default=60, help='Timeout for HTTP requests in seconds')
    parser.add_argument('--num-http-retries', '-r', type=int, default=3, help='Number of retries for HTTP requests')
    args = parser.parse_args()

    archive_type = args.archive_type
    output_file = None
    mosaic_fname = get_filename_from_url(args.mosaic_url)

    if args.output_file:
        output_file = Path(args.output_file)
        if output_file.suffix == '.mbtiles':
            if archive_type and archive_type != 'mbtiles':
                raise ValueError("Output file extension is .mbtiles but --archive-type is not 'mbtiles'")
            archive_type = 'mbtiles'
        elif output_file.suffix == '.pmtiles':
            if archive_type and archive_type != 'pmtiles':
                raise ValueError("Output file extension is .pmtiles but --archive-type is not 'pmtiles'")
            archive_type = 'pmtiles'
        else:
            raise ValueError("If --output-file is provided, it must have a .mbtiles or .pmtiles extension.")

    if not archive_type:
        raise ValueError("You must specify either --output-file (with .mbtiles or .pmtiles extension) or --archive-type.")

    if not output_file:
        if not mosaic_fname.endswith('.mosaic.json'):
            raise ValueError('Input mosaic URL must point to a file ending with .mosaic.json if --output-file is not specified.')
        
        out_fname = mosaic_fname[:-len('.mosaic.json')] + '.' + archive_type
        output_file = Path(out_fname)


    merger = Merger(args.mosaic_url,
                    archive_type, 
                    output_file, 
                    args.request_timeout_secs, 
                    args.num_http_retries)

    merger.process()
    merger.cleanup()

    print('Done!!!')
    

if __name__ == '__main__':
    cli()

