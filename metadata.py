#!/usr/bin/env python3

'''
Install ffmpeg (ffprobe.exe into this directory)
pip install pillow ffmpeg-python
'''

import os
import sys
from pprint import pprint

import ffmpeg
import PIL.Image
import PIL.ExifTags

class MediaFile(object):
    ''' Multimedia object (image or movie) '''

    def __init__(self, path):
        self.path = path
        self.name, self.extension = os.path.splitext(os.path.basename(path))
        self.extension = self.extension[1:]  # remove the '.'
        self.location = None
        self.size = os.path.getsize(path)

        if self.extension.lower() in ['jpg', 'jpeg', 'png']:
            img = PIL.Image.open(self.path)
            img.load()
            if img._getexif():
                pprint({ PIL.ExifTags.TAGS[k]: v for k, v in img._getexif().items() if k in PIL.ExifTags.TAGS })
        elif self.extension.lower() in ['mp4', 'mov', 'avi' ]:
            pprint(ffmpeg.probe(self.path))

if __name__ == '__main__':
    media = MediaFile(sys.argv[1])
