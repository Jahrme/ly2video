FROM ubuntu:bionic

RUN apt-get update \
&& \
apt-get install --yes \
alsa \
ffmpeg \
libasound-dev \
lilypond \
python-pil \
python-pip \
swig \
timidity \
&& \
pip install \
Pillow \
python-midi

COPY . /ly2video/

WORKDIR /ly2video/

RUN python setup.py install
