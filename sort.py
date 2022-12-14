#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''
Install ffmpeg (ffprobe.exe into this directory)
pip install pillow geopy ffmpeg-python
'''

__author__ = "Paul Wichern"
__license__ = "MIT"
__version__ = "1.0.0"

from typing import List
import argparse
import os
import glob
import datetime
import logging
import pickle
import shutil
import filecmp
import signal
import traceback

import ffmpeg
import geopy
import PIL.Image
import PIL.ExifTags

INTERRUPT_PICKLE = 'interrupt.pickle'

# Setup logging to file
logging.basicConfig(
    filename='fotos.log',
    level=logging.INFO,
    encoding='utf-8',
    format= '[%(asctime)s] {%(pathname)s:%(lineno)-3d} %(levelname)-8s - %(message)s',
    datefmt='%H:%M:%S')

# Add logging to stdout
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(levelname)-8s %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)

class GeoLocator():
    ''' This class can return the address of geolocation. '''

    pickle_file = 'coordinates.pickle'
    coordinates = {}

    def __init__(self, user_agent: str = 'fotos.py'):
        self.geolocator = geopy.geocoders.Nominatim(user_agent=user_agent)

        if os.path.exists(self.pickle_file):
            logging.info('Load %s ...', self.pickle_file)
            with open(self.pickle_file, 'rb') as pfile:
                self.coordinates = pickle.load(pfile)

    def persist(self):
        ''' Write already fetched locations into pickle file. '''
        with open(self.pickle_file, 'wb') as pfile:
            pickle.dump(self.coordinates, pfile)

    def address(self, latitude: float, longitude: float):
        ''' Get address of geolocation '''
        # Shorten coordinates by rounding.
        coordinates = f'{round(latitude, 3)},{round(longitude, 3)}'

        if coordinates in self.coordinates:
            logging.debug('location from cache')
            return self.coordinates[coordinates]

        try:
            location = self.geolocator.reverse(f'{latitude},{longitude}').raw['address']
            self.coordinates[coordinates] = location
            self.persist()
        except ValueError:
            logging.error('lat: %s and lon: %s are not correct.', latitude, longitude)
            return None

        return location

class UnknownMedia(Exception):
    '''Raised when trying to open a file that is not a known image or movie file type.'''

class DuplicateException(Exception):
    ''' Raised when a file with the same name and content already exists. '''

class MediaFile(object):
    ''' Multimedia object (image or movie) '''

    def __init__(self, filepath: str, locations: GeoLocator):
        self.path = filepath
        self.name, self.extension = os.path.splitext(os.path.basename(filepath))
        self.extension = self.extension[1:]  # remove the '.'
        self.location = None
        self.size = os.path.getsize(filepath)

        if self.extension.lower() in ['jpg', 'jpeg', 'png']:
            self.exif = self.__read_exif()
            self.location = self.__exif_location(locations)
            self.date = self.__exif_date()
        elif self.extension.lower() in ['mp4', 'mov', 'avi' ]:
            self.metadata = self.__read_metadata()
            self.location = self.__metadata_location(locations)
            self.date = self.__metadata_date()
        else:
            raise UnknownMedia()

    def __read_exif(self):
        ''' Read image EXIF data '''
        try:
            img = PIL.Image.open(self.path)
            img_exif = img._getexif()
            if img_exif:
                return {
                    PIL.ExifTags.TAGS[k]: v
                    for k, v in img_exif.items()
                    if k in PIL.ExifTags.TAGS
                }
        except OSError as os_error:
            logging.error(f'%s: %s', self.path, os_error)
        return {}

    def __exif_location(self, locations: GeoLocator) -> str:
        ''' Extract location from EXIF '''
        if 'GPSInfo' not in self.exif:
            return None

        gpsinfo = self.exif['GPSInfo']

        try:
            degrees, minutes, seconds = gpsinfo[2]
            latitude = float(degrees) + float(minutes) / float(60) + float(seconds) / float(3600)

            degrees, minutes, seconds = gpsinfo[4]
            longitude = float(degrees) + float(minutes) / float(60) + float(seconds) / float(3600)

            return self.__address2location(locations.address(latitude, longitude))
        except KeyError:
            logging.error('%s: GPSInfo not as expected: "%s"', self.path, str(gpsinfo))
            return None

    def __read_metadata(self):
        ''' Read metadata from movie file '''
        try:
            return ffmpeg.probe(self.path)
        except Exception as ffmpeg_exception:
            logging.error('%s: Reading metadata failed: "%s"', self.path, str(ffmpeg_exception))
            return {}

    def __metadata_date(self):
        ''' Extract date from movie metadata '''
        # We value the date encoded in the filename more than the one in the exif data.
        date = self.__guess_date_by_filename()
        if date:
            return date

        if not 'format' in self.metadata:
            return date

        metadata_format = self.metadata['format']
        if not 'tags' in metadata_format:
            return date

        tags = metadata_format['tags']
        if not 'creation_time' in tags:
            return date

        creation_time = tags['creation_time']

        # Try multiple format strings
        for format_str in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%fZ']:
            try:
                return datetime.datetime.strptime(creation_time, format_str)
            except ValueError:
                continue

        logging.error('%s: Unknown date format in metadata: "%s".', self.path, creation_time)
        return date

    def __iso6709(self, val: str) -> List[str]:
        ''' Convert ISO-6709 Geolocation string into list of latitude, longitude, height. '''
        ret = []
        part = ''
        for char in val:
            if char in ('+', '-') and part != '':
                ret.append(part)
                part = ''
            part += char

        if part != '':
            if part.endswith('/'):
                part = part[:-1]
            ret.append(part)

        return ret

    def __metadata_location(self, locations: GeoLocator):
        ''' Get location from movie metadate '''
        if not 'format' in self.metadata:
            return None

        metadata_format = self.metadata['format']
        if not 'tags' in metadata_format:
            return None

        metadata_tags = metadata_format['tags']
        if 'location' in metadata_tags:
            location = metadata_tags['location']
        elif 'com.apple.quicktime.location.ISO6709' in metadata_tags:
            location = metadata_tags['com.apple.quicktime.location.ISO6709']
        else:
            return None

        geolocation = self.__iso6709(location)
        if len(geolocation) >= 2:
            return self.__address2location(
                locations.address(
                    float(geolocation[0]),
                    float(geolocation[1])))
        logging.error('%s: Unexpected location format in metadata: "%s"', self.path, location)
        return None

    def __exif_date(self):
        ''' Get date from image exif data '''
        # We value the date encoded in the filename more than the one in the exif data.
        date = self.__guess_date_by_filename()
        if date:
            return date

        if 'DateTimeOriginal' not in self.exif:
            return date

        datetimeorig = self.exif['DateTimeOriginal']
        try:
            return datetime.datetime.strptime(datetimeorig, '%Y:%m:%d %H:%M:%S')
        except ValueError:
            logging.error('%s: Unknown datetime in exif data: "%s"', self.path, datetimeorig)
            return date

    def __address2location(self, address) -> str:
        ''' Get location name from address object '''
        if not address:
            return None
        if 'suburb' in address:
            if 'village' in address:
                return address['village'] + '_' + address['suburb']
            if 'town' in address:
                return address['town'] + '_' + address['suburb']
            return address['suburb']
        if 'village' in address:
            return address['village']
        if 'town' in address:
            return address['town']
        if 'state' in address:
            return address['state']
        logging.warning('%s: Could not determine location from address: %s',
            self.path, str(address))
        return None

    def __dest_directory(self, dst_base: str) -> str:
        ''' Return dest directory of this file '''
        directory = dst_base

        if self.date:
            directory += self.date.strftime('/%Y/%m')
        else:
            directory += '/0000'

        if self.location:
            directory += '/' + self.location

        return directory

    def __dest_name(self, duplicate: int) -> str:
        ''' Get the dest file name '''
        if 0 == duplicate:
            return f'{self.name}.{self.extension}'
        return f'{self.name}_{duplicate}.{self.extension}'

    def copy(self, dst_base: str):
        ''' Copy the file into its dest directory '''
        # Get full dest directory
        directory = self.__dest_directory(dst_base)

        # Create dest directory
        if not os.path.exists(directory):
            os.makedirs(directory)

        # Add a suffix to the filename until a new filename was found.
        duplicate = 0
        while True:
            filename = self.__dest_name(duplicate)

            dst = directory + '/' + filename
            if os.path.exists(dst):
                if filecmp.cmp(dst, self.path):
                    raise DuplicateException(
                        f'{dst}: A file with the same name and content already exists.')
                duplicate += 1
            else:
                logging.debug('%s -> %s', self.path, dst)
                shutil.copyfile(self.path, dst)
                break

    def __guess_date_by_filename(self):
        ''' Guess the media date by its filename '''
        if self.name.startswith('IMG_'):
            try:
                date = datetime.datetime.strptime(self.name.split('_')[1], '%Y%m%d')
                # Validate
                if date.year >= 1990 and date.year <= datetime.date.today().year:
                    return date
            except ValueError:
                pass
        elif len(self.name) > 8:
            # Sometimes filenames start with the date.
            try:
                date = datetime.datetime.strptime(self.name[:8], '%Y%m%d')
                # Validate
                if date.year >= 1990 and date.year <= datetime.date.today().year:
                    return date
            except ValueError:
                pass
        return None

# Whether to interrupt the sorting.
interrupt_sort = False

def signal_handler(signum, frame):
    '''
    Interrupt handler for CTRL-C
    '''
    global interrupt_sort
    interrupt_sort = True

# Define signal handler
signal.signal(signal.SIGINT, signal_handler)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        prog = 'Photo Sorter',
        description = 'Sort photos by date and location.')
    parser.add_argument('source_directory', help='Directory from which to get the images.')
    parser.add_argument('dest_directory', help='Directory to which to copy/sort the images to.')
    parser.add_argument(
        '-r', '--recursive',
        help='Scan source directory recursively',
        action='store_true')  # on/off flag
    args = parser.parse_args()

    source_directory = args.source_directory
    if args.recursive:
        source_directory += '/**'
    else:
        source_directory += '/*'

    locator = GeoLocator()

    logging.info('Scanning %s ...', source_directory)

    stats = {
        'source': source_directory,
        'paths': set(),
        'bytes': 0,
        'duplicates': 0
    }

    # Try to load a previous state.
    if os.path.exists(INTERRUPT_PICKLE):
        with open(INTERRUPT_PICKLE, 'rb') as file:
            stats_loaded = pickle.load(file)
            if stats_loaded['source'] == source_directory:
                user_input = input('Continue interrupted run? [Yn]')
                if user_input != 'n':
                    stats = stats_loaded

    interrupted = False
    for path in glob.iglob(source_directory, recursive=True):
        if interrupt_sort:
            logging.info('Keyboard interrupt')
            with open(INTERRUPT_PICKLE, 'wb') as file:
                pickle.dump(stats, file)
                interrupted = True
                break

        if path in stats['paths']:
            continue

        # Only interested in files
        if not os.path.isfile(path):
            continue

        logging.info('[%s, %.2fGB, %sdups] %s', 
            len(stats['paths']),
            stats['bytes'] / 1024 / 1024 / 1024,
            stats['duplicates'],
            path)

        try:
            media = MediaFile(path, locator)
            media.copy(args.dest_directory)
            stats['paths'].add(path)
            stats['bytes'] += media.size
        except geopy.exc.GeocoderUnavailable:
            logging.error('Could not fetch geolocation (too many requests?)')
            interrupted = True
            break
        except UnknownMedia:
            logging.warning('%s ignored', path)
            stats['paths'].add(path)
        except DuplicateException as duplicate_exception:
            logging.warning(duplicate_exception)
            stats['duplicates'] += 1
            stats['paths'].add(path)
        except Exception as general_exception:
            # TODO: print whole stack
            logging.error('Sorting media failed: %s\n%s', general_exception, traceback.format_exc())
            interrupted = True
            break

    if interrupted:
        with open(INTERRUPT_PICKLE, 'wb') as file:
            pickle.dump(stats, file)
    else:
        if os.path.exists(INTERRUPT_PICKLE):
            logging.info('Remove %s', INTERRUPT_PICKLE)
            os.remove(INTERRUPT_PICKLE)
