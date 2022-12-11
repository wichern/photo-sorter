#!/usr/bin/env python3

'''
Install ffmpeg (ffprobe.exe into this directory)
pip install pillow geopy ffmpeg-python
'''

from typing import Dict, Tuple, List
import os
import glob
import geopy
import PIL.Image
import PIL.ExifTags
import sys
import datetime
import logging
import pickle
import ffmpeg
import shutil
import time
import filecmp

IMAGE_FILE_EXTENSIONS = ['jpg', 'jpeg', 'png']
MOVIE_FILE_EXTENSIONS = ['mp4', 'mov', 'avi' ]

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

# TODO: Write Readme.md
# TODO: pylint

class GeoLocator(object):
    '''
    This class can return the address of geolocation.
    '''

    pickle_file = 'coordinates.pickle'
    coordinates = dict()

    def __init__(self, user_agent: str = 'fotos.py'):
        self.geolocator = geopy.geocoders.Nominatim(user_agent=user_agent)

        if os.path.exists(self.pickle_file):
            logging.info(f'Load {self.pickle_file} ...')
            with open(self.pickle_file, 'rb') as file:
                self.coordinates = pickle.load(file)

    def persist(self):
        with open(self.pickle_file, 'wb') as file:
            pickle.dump(self.coordinates, file)

    def address(self, latitude: float, longitude: float):
        coordinates = f'{latitude},{longitude}'

        if coordinates in self.coordinates:
            logging.debug('location from cache')
            return self.coordinates[coordinates]

        location = self.geolocator.reverse(f'{latitude},{longitude}').raw['address']
        self.coordinates[coordinates] = location
        return location

class NotAnImageException(Exception):
    'Raised when trying to open a file that is not a known image file type.'
    pass

class NotAMovieException(Exception):
    'Raised when trying to open a file that is not a known movie file type.'
    pass

class MediaFile(object):
    def __init__(self, path: str, locations: GeoLocator):
        if not os.path.exists(path):
            raise FileNotFoundError(path)

        self.path = path
        self.name, self.extension = os.path.splitext(os.path.basename(path))
        self.extension = self.extension[1:]  # remove the '.'

    def _address2location(self, address) -> str:
        if 'suburb' in address:
            return address['suburb']
        if 'village' in address:
            return address['village']
        if 'town' in address:
            return address['town']
        if 'state' in address:
            return address['state']
        logging.warning('Could not determine location from address: ' + str(address))
        return None

    def __dest(self, duplicate_count: int = 0) -> Tuple[str, str]:
        dirname = self.date.strftime('%Y/%m/')
        if self.location:
            dirname += self.location

        filename = self.name
        if duplicate_count > 0:
            name_without_ext, extension = os.path.splitext(img.name)
            filename = name_without_ext + '_' + str(duplicate_count) + extension

        return dirname, filename

    def __dest_directory(self, dst_base: str) -> str:
        directory = dst_base

        if self.date:
            directory += self.date.strftime('/%Y/%m')
        else:
            directory += '/0000'

        if self.location:
            directory += '/' + self.location
        
        return directory

    def __dest_name(self, duplicate: int) -> str:
        if 0 == duplicate:
            return f'{self.name}.{self.extension}'
        return f'{self.name}_{duplicate}.{self.extension}'

    def copy(self, dst_base: str):
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
                    logging.warning(f'{dst}: A file with the same name and content already exists.')
                    break  # We ignore this file
                else:
                    duplicate += 1
            else:
                logging.info(' -> ' + dst)
                shutil.copyfile(self.path, dst)
                break

    def _guess_date_by_filename(self):
        if self.name.startswith('IMG_'):
            logging.debug(f'Guess date from "{self.name}"')
            try:
                date = datetime.datetime.strptime(self.name.split('_')[1], '%Y%m%d')
                # Validate
                if date.year >= 1990 and date.year <= datetime.date.today().year:
                    return date
            except ValueError:
                return None
        return None

class Image(MediaFile):
    '''
    An image object
    '''

    def __init__(self, path: str, locations: GeoLocator):
        super().__init__(path, locations)

        if not self.extension.lower() in IMAGE_FILE_EXTENSIONS:
            raise NotAnImageException()

        logging.info(f'Loading {path}')

        self.exif = self.__read_exif()
        self.location = self.__location(locations)
        self.date = self.__date()
    
    def __read_exif(self):
        try:
            img = PIL.Image.open(self.path)
            img.load()
            if img._getexif():
                return { PIL.ExifTags.TAGS[k]: v for k, v in img._getexif().items() if k in PIL.ExifTags.TAGS }
        except OSError as os_error:
            logging.error(f'{self.path}: {os_error}')
        return dict()

    def __location(self, locations: GeoLocator) -> str:
        if 'GPSInfo' in self.exif:
            gpsinfo = self.exif['GPSInfo']

            try:
                degrees, minutes, seconds = gpsinfo[2]
                latitude = float(degrees) + float(minutes) / float(60) + float(seconds) / float(3600)

                degrees, minutes, seconds = gpsinfo[4]
                longitude = float(degrees) + float(minutes) / float(60) + float(seconds) / float(3600)

                return super()._address2location(locations.address(latitude, longitude))
            except KeyError:
                logging.error(f'{self.path}: GPSInfo not as expected: "{str(gpsinfo)}"')
        return None

    def __date(self):
        # We value the date encoded in the filename more than the one in the exif data.
        date = super()._guess_date_by_filename()
        if not date:
            if 'DateTimeOriginal' in self.exif:
                datetimeorig = self.exif['DateTimeOriginal'] 
                try:
                    return datetime.datetime.strptime(datetimeorig, '%Y:%m:%d %H:%M:%S')
                except ValueError:
                    logging.error(f'Unknown datetime in exif data of {self.path}: "{datetimeorig}"')
        return date

class Movie(MediaFile):
    def __init__(self, path: str, locations: GeoLocator):
        super().__init__(path, locations)

        if not self.extension.lower() in MOVIE_FILE_EXTENSIONS:
            raise NotAMovieException()

        logging.info(f'Loading {path}')

        self.metadata = self.__read_metadata()
        self.location = self.__location(locations)
        self.date = self.__date()

    def __read_metadata(self):
        try:
            return ffmpeg.probe(self.path)
        except Exception as ffmpeg_exception:
            logging.error(f'{self.path}: Reading metadata failed: "{str(ffmpeg_exception)}"')
            return dict()

    def __date(self):
        # We value the date encoded in the filename more than the one in the exif data.
        date = super()._guess_date_by_filename()
        if not date:
            if 'format' in self.metadata:
                format = self.metadata['format']
                if 'tags' in format:
                    tags = format['tags']
                    if 'creation_time' in tags:
                        creation_time = tags['creation_time']
                        try:
                            return datetime.datetime.strptime(creation_time, '%Y-%m-%dT%H:%M:%S.%f')
                        except ValueError:
                            try:
                                return datetime.datetime.strptime(creation_time, '%Y-%m-%d %H:%M:%S')
                            except ValueError:
                                try:
                                    return datetime.datetime.strptime(creation_time, '%Y-%m-%dT%H:%M:%S.%fZ')
                                except ValueError:
                                    logging.error(f'Unknown date format in metadata of {self.path}: "{creation_time}".')
        return date

    def __iso6709(self, val: str) -> List[str]:
        ret = []
        
        part = ''
        for c in val:
            if (c == '+' or c == '-') and part != '':
                ret.append(part)
                part = ''
            part += c
            
        if part != '':
            ret.append(part)

        return ret

    def __location(self, locations: GeoLocator):
        if 'format' in self.metadata:
            format = self.metadata['format']
            if 'tags' in format:
                tags = format['tags']
                if 'location' in tags:
                    location = tags['location']
                elif 'com.apple.quicktime.location.ISO6709' in tags:
                    location = tags['com.apple.quicktime.location.ISO6709']
                else:
                    return None

                geolocation = self.__iso6709(location)
                if len(geolocation) >= 2:
                    return super()._address2location(locations.address(geolocation[0], geolocation[1]))
                else:
                    logging.error(f'Unexpected location format in metadata of {self.path}: "{location}"')
        return None

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(f'Usage: {sys.argv[0]} SRC_DIR DST_DIR')
        sys.exit(1)

    src_dir = sys.argv[1] + '/**'
    dst_dir = sys.argv[2]

    locator = GeoLocator()

    logging.info(f'Scanning {src_dir} ...')
    for path in glob.iglob(src_dir, recursive=True):
        # Only interested in files
        if not os.path.isfile(path):
            continue

        try:
            img = Image(path, locator)
            img.copy(dst_dir)
            locator.persist()
        except NotAnImageException:
            try:
                mov = Movie(path, locator)
                mov.copy(dst_dir)
            except NotAMovieException:
                logging.warning(f'{path} ignore')

    locator.persist()
