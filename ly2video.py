#!/usr/bin/env python
# coding=utf-8

# ly2video - generate performances video from LilyPond source files
# Copyright (C) 2012 Jiri "FireTight" Szabo
# Copyright (C) 2012 Adam Spiers
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# For more information about this program, please visit
# <https://github.com/aspiers/ly2video/>.

import collections
import copy
import os
import re
import shutil
import subprocess
import sys
import urllib
from distutils.version import StrictVersion
from optparse import OptionParser
from struct import pack

from PIL import Image, ImageDraw, ImageFont
from ly.tokenize import MusicTokenizer, Tokenizer
import ly.tools
from pyPdf import PdfFileWriter, PdfFileReader
import midi

from pprint import pprint, pformat

DEBUG = False # --debug sets to True

C_MAJOR_SCALE_STEPS = {
    # Maps notes of the C major scale into semi-tones above C.
    # This is needed to map the pitch of ly.tools.Pitch notes
    # into MIDI pitch values within a given octave.
    0 :  0, # c
    1 :  2, # d
    2 :  4, # e
    3 :  5, # f
    4 :  7, # g
    5 :  9, # a
    6 : 11, # b
}

def findTopStaffLine(image, lineLength):
    """
    Returns the coordinates of the left-most pixel in the top line of
    the first staff in the image.

    FIXME: The code assumes that the first staff is not indented
    further right than subsequent staffs.

    Params:
    - image:        image with staff lines
    - lineLength:   needed length of line to accept it as staff line
    """
    # position of the first line on image
    firstLinePos = (-1, -1)

    width, height = image.size

    # Start searching at the hard left but allow for a left margin.
    for x in xrange(width):
        for y in xrange(height):
            for length in xrange(lineLength):
                # testing color of pixels in range (startPos, startPos + lineLength)
                if image.getpixel((x + length, y)) == (255,255,255):
                    # if it's white then it's not a staff line
                    firstLinePos = (-1, -1)
                    break
                else:
                    # else it can be
                    firstLinePos = (x, y)
            # when have a valid position, break out
            if firstLinePos != (-1, -1):
                break
        if firstLinePos != (-1, -1):
            break

    progress("First staff line found at (%d, %d)" % firstLinePos)
    return firstLinePos

def lineIndices(imageFile, lineLength):
    """
    Takes a image and returns height indices of staff lines in pixels.

    Params:
    - imageFile:    name of image with staff lines
    - lineLength:   needed length of line to accept it as staff line
    """
    progress("Looking for staff lines in %s" % imageFile)
    image = Image.open(imageFile)
    width, height = image.size

    firstLineX, firstLineY = findTopStaffLine(image, lineLength)
    # move 3 pixels to the right, to avoid line of pixels connectings
    # all staffs together
    firstLineX += 3

    lines = []
    newLine = True

    for y in xrange(firstLineY, height):
        # if color of that pixel isn't white
        if image.getpixel((firstLineX, y)) != (255,255,255):
            # and it can be new staff line
            if newLine:
                # accept it
                newLine = False
                lines.append(y)
        else:
            # it's space between lines
            newLine = True

    del image

    # return staff line indices
    return lines

def generateTitle(titleText, width, height, fps, titleLength):
    """
    Generates frames with name of song and its author.

    Params:
    - titleText:    collection of name of song and its author
    - width:        pixel width of frames (and video)
    - height        pixel height of frames (and video)
    - fps:          frame rate (frames per second) of final video
    - titleLength:  length of title screen (seconds)
    """

    # create image of title screen
    titleScreen = Image.new("RGB", (width, height), (255,255,255))
    # it will draw text on titleScreen
    drawer = ImageDraw.Draw(titleScreen)
    # save folder for frames
    if not os.path.exists("title"):
        os.mkdir("title")

    totalFrames = fps * titleLength
    progress("TITLE: ly2video will generate approx. %d frames." % totalFrames)

    # font for song's name, args - font type, size
    nameFont = ImageFont.truetype("arial.ttf", height / 15)
    # font for author
    authorFont = ImageFont.truetype("arial.ttf", height / 25)

    # args - position of left upper corner of rectangle (around text), text, font and color (black)
    drawer.text(((width - nameFont.getsize(titleText.name)[0]) / 2,
                 (height - nameFont.getsize(titleText.name)[1]) / 2 - height / 25),
                titleText.name, font=nameFont, fill=(0,0,0))
    # same thing
    drawer.text(((width - authorFont.getsize(titleText.author)[0]) / 2,
                 (height / 2) + height / 25),
                titleText.author, font=authorFont, fill=(0,0,0))

    # generate needed number of frames (= fps * titleLength)
    for frameNum in xrange(totalFrames):
        titleScreen.save(tmpPath("title", "frame%d.png" % frameNum))

    progress("TITLE: Generating title screen has ended. (%d/%d)" %
             (totalFrames, totalFrames))
    return 0

def writePaperHeader(fFile, width, height, numOfLines, lilypondVersion):
    """
    Writes own paper block into given file.

    Params:
    - fFile:        given opened file
    - width:        pixel width of frames (and video)
    - height        pixel height of frames (and video)
    - numOfLines:   number of staff lines
    """

    pixelsPerMm = 181.0 / 720 # 1 px = 0.251375 mm

    fFile.write("\\paper {\n")

    # one-line-breaking is available as of 2.15.41:
    #   https://code.google.com/p/lilypond/issues/detail?id=2570
    #   https://codereview.appspot.com/6248056/
    #   http://article.gmane.org/gmane.comp.gnu.lilypond.general/72373/
    oneLineBreaking = False
    if StrictVersion(lilypondVersion) >= StrictVersion('2.15.41'):
        oneLineBreaking = True
    else:
        warn("""you have LilyPond %s which does not support
infinitely long lines.  Upgrade to >= 2.15.41 to avoid
sudden jumps in your video.
""" % lilypondVersion)

    if oneLineBreaking:
        fFile.write("   page-breaking = #ly:one-line-breaking\n")
    else:
        fFile.write("   paper-width   = %d\\mm\n" % round(10 * width * pixelsPerMm))
        fFile.write("   paper-height  = %d\\mm\n" % round(height * pixelsPerMm))

    fFile.write("   top-margin    = %d\\mm\n" % round(height * pixelsPerMm / 20))
    fFile.write("   bottom-margin = %d\\mm\n" % round(height * pixelsPerMm / 20))
    fFile.write("   left-margin   = %d\\mm\n" % round(width * pixelsPerMm / 2))
    fFile.write("   right-margin  = %d\\mm\n" % round(width * pixelsPerMm / 2))

    if not oneLineBreaking:
        fFile.write("   print-page-number = ##f\n")

    fFile.write("}\n")
    fFile.write("#(set-global-staff-size %d)\n\n" %
                int(round((height - 2 * (height / 10)) / numOfLines)))

    return 0

def getTemposList(midiFile):
    """
    Returns a list of tempo changes in midiFile.  Each tempo change is
    represented as a (tick, tempoValue) tuple.
    """
    midiHeader = midiFile[0]

    temposList = []
    for event in midiHeader:
        # if it's SetTempoEvent
        if isinstance(event, midi.SetTempoEvent):
            # convert value from hexadecimal into decimal
            base = 0
            tempoValue = 0
            data = event.data
            data.reverse()
            for value in data:
                tempoValue += value * (256 ** base)
                base += 1
            # and add that new tempo with its start into temposList
            temposList.append((event.tick, tempoValue))

    return temposList

def getNotesInTicks(midiFile):
    """
    Returns a dict mapping ticks to a list of NoteOn events in that tick.
    """
    notesInTicks = {}

    # for every channel in MIDI (except the first one)
    for eventsList in midiFile[1:]:
        for event in eventsList:
            if not isinstance(event, midi.NoteOnEvent):
                continue

            if event.get_velocity() == 0:
                # velocity is zero (that's basically "NoteOffEvent")
                continue

            # add it into notesInTicks
            if event.tick not in notesInTicks:
                notesInTicks[event.tick] = []
            notesInTicks[event.tick].append(event)

    return notesInTicks

def getMidiEvents(midiFileName):
    """
    Extracts useful information from a given MIDI file and returns it.

    Params:
      - midiFileName: name of MIDI file (string)

    Returns a tuple of the following items:
      - midiResolution: the resolution of the MIDI file
      - temposList: as returned by getTemposList()
      - notesInTicks: as returned by getNotesInTicks()
      - midiTicks: a sorted list of which ticks contain NoteOn events.
                   The last tick corresponds to the earliest
                   EndOfTrackEvent found across all MIDI channels.
    """

    # open MIDI with external library
    midiFile = midi.read_midifile(midiFileName)
    # and make ticks absolute
    midiFile.make_ticks_abs()

    # get MIDI resolution and header
    midiResolution = midiFile.resolution

    temposList = getTemposList(midiFile)
    notesInTicks = getNotesInTicks(midiFile)

    # get all ticks with notes and sorts it
    midiTicks = notesInTicks.keys()
    midiTicks.sort()

    # find the tick corresponding to the earliest EndOfTrackEvent
    # across all MIDI channels, and append it
    endOfTrack = -1
    for eventsList in midiFile[1:]:
        if isinstance(eventsList[-1], midi.EndOfTrackEvent):
            if endOfTrack < eventsList[-1].tick:
                endOfTrack = eventsList[-1].tick
    midiTicks.append(endOfTrack)

    progress("MIDI: Parsing MIDI file has ended.")

    return (midiResolution, temposList, notesInTicks, midiTicks)

def getNotePositions(pdfFileName, lySrcFileName, lySrcLines):
    """
    For every link annotation in the PDF file which is a link to the
    sanitised .ly file we generated, store the coordinates of the
    annotated rectangle and also the line and column number it points
    to in the .ly file.

    Parameters:
      - pdfFileName
      - lySrcLines: loaded *.ly file in memory (list)

    Returns:
      - notesAndTies: a list of (lineNum, charNum) tuples sorted by
        line number in the .ly file
      - notePositionsByPage: a list with each top-level item
        representing a page, where each page is a sorted list of
        ((lineNum, charNum), coords) tuples.  coords is (x1,y1,x2,y2)
        representing opposite corners of the rectangle.
      - tokens: a dict mapping every (lineNum, charNum) tuple to the
        token found at that point in the .ly source.  This will be used
        to compare notes in the source with notes in the MIDI
      - parser: the MusicTokenizer() object which can be reused for
        pitch calculations
      - pageWidth: the width of the first PDF page in PDF units (all
        pages are assumed to have the same width)
    """

    progress(("Extracting annotation positions from:\n    %s\n" +
              "and corresponding source positions in:\n    %s") %
             (pdfFileName, lySrcFileName))

    escapedLySrcFileName = urllib.quote(lySrcFileName)

    # open PDF file with external library and gets width of page (in PDF measures)
    fPdf = file(pdfFileName, "rb")
    pdfFile = PdfFileReader(fPdf)
    numPages = pdfFile.getNumPages()
    progress("PDF file %s has %d page(s)" % (pdfFileName, numPages))
    pageWidth = pdfFile.getPage(0).getObject()['/MediaBox'][2]
    progress("Width of first PDF page is %f" % pageWidth)

    notesAndTies = set()
    notePositionsByPage = []
    tokens = {}

    for pageNumber in xrange(numPages):
        # get informations about page
        page = pdfFile.getPage(pageNumber)
        info = page.getObject()

        # ly parser (from Frescobaldi)
        parser = MusicTokenizer()

        if not info.has_key('/Annots'):
            continue

        links = info['/Annots']

        # stores wanted positions on single page
        notePositionsInPage = []

        for link in links:
            # Get (x1, y1, x2, y2) coordinates of opposite corners
            # of the annotated rectangle
            coords = link.getObject()['/Rect']
            # if it's not link into .ly, then ignore it
            uri = link.getObject()['/A']['/URI']
            if uri.find(escapedLySrcFileName) == -1:
                continue
            # otherwise get coordinates into .ly file
            lineNum, charNum, columnNum = uri.split(":")[-3:]
            lineNum   = int(lineNum)
            charNum   = int(charNum)   # the start of the text
            columnNum = int(columnNum) # the end of the text?
            srcLine = lySrcLines[lineNum - 1]

            try:
                # get name of note
                token = parser.tokens(srcLine[charNum:]).next()

                # Is the note immediately followed by \rest?  If so,
                # it's actually a rest not a note:
                # http://lilypond.org/doc/v2.14/Documentation/notation/writing-rests
                # We default to assuming it's a note, in case there
                # isn't any other note to the right of it.
                isNote = True

                restOfLine = srcLine[charNum + len(token):]
                for rightToken in parser.tokens(restOfLine):
                    # if there is another note (or rest etc.) to the
                    # right of it, it's a real note
                    if isinstance(rightToken, MusicTokenizer.Pitch):
                        break
                    # if \rest appears after it and before the next
                    # note, it's a rest not a note, so we ignore it
                    elif isinstance(rightToken, Tokenizer.Command) and \
                         rightToken == '\\rest':
                        isNote = False
                        break

                # If the note is not followed by \rest, and it's a
                # note rather than an "r"-style rest or it's a tie, we
                # keep track of it.  In the next phase,
                # getFilteredIndices() will filter out notes to the
                # right of ties.
                if isNote:
                    isNote = isinstance(token, MusicTokenizer.Pitch) and \
                             str(token) not in "rR"
                    if isNote or token == '~':
                        # add it
                        sourceCoords = (lineNum, charNum)
                        notePositionsInPage.append((sourceCoords, coords))
                        notesAndTies.add(sourceCoords)
                        tokens[sourceCoords] = token

            #if there is some error, write that statement and exit
            except StandardError as err:
                fatal(("PDF: %s\n"
                       + "ly2video was trying to work with this: "
                       + "\"%s\", coords in .ly (line %d char %d).") %
                      (err, lySrcLines[lineNum - 1][charNum:][:-1],
                       lineNum, charNum))

        # sort wanted positions on that page and add it into whole wanted positions
        notePositionsInPage.sort()
        notePositionsByPage.append(notePositionsInPage)

    # close PDF file
    fPdf.close()

    # create list of notes and ties and sort it
    notesAndTies = list(notesAndTies)
    notesAndTies.sort()
    return notePositionsByPage, notesAndTies, tokens, parser, pageWidth

def getFilteredIndices(notePositionsByPage, notesAndTies, lySrcLines, imageWidth, pageWidth):
    """
    Goes through notePositionsByPage, filtering out anything that
    won't generate a MIDI NoteOn event, converting each note's
    coordinate into an index (i.e. the x-coordinate of the center of
    the note in the PNG file which contains it), and merging indices
    which are within +/- 10 pixels of each other.

    Parameters
      - notePositionsByPage: a list with each top-level item
        representing a page, where each page is a sorted list of
        ((lineNum, charNum), coords) tuples.  coords is (x1,y1,x2,y2)
        representing opposite corners of the rectangle.
      - notesAndTies: a list of (lineNum, charNum) tuples sorted by
        line number in sanitised .ly file
      - lySrcLines: loaded *.ly file in memory (list)
      - imageWidth: width of PNG file(s)
      - pageWidth: the width of the first PDF page in PDF units (all
        pages are assumed to have the same width)

    Returns:
      - indexNoteSourcesByPage:
            a list of dicts, one per page, mapping each index to a
            list of (lineNum, colNum) tuples in the .ly source file
            corresponding to the notes at that index, e.g.
                [
                    # page 1
                    {
                        123 : [    # index
                            (37, 2), # note at index 123, line 37 col 2
                            (37, 5), # note at index 123, line 37 col 5
                        ],
                        ...
                    }

                    # page 2
                    {
                        ...
                    }
                ]
      - noteIndicesByPage:
            a list of sorted lists, one per page, containing
            all the indices on that page in order, e.g.
                [
                    # page 1
                    [ 123, 137, 178 ... ],
                    # page 2
                    [ ... ],
                ]
    """
    indexNoteSourcesByPage = []
    noteIndicesByPage = []

    for pageNum, notePositionsInPage in enumerate(notePositionsByPage):
        parser = Tokenizer()
        # co-ordinates in the .ly source of notes, grouped by index
        # (within one page)
        indexNoteSourcesInPage = {}

        # Notes that are preceded by tie and will not generate
        # a MIDI NoteOn event
        silentNotes = []

        for (linkLy, coords) in notePositionsInPage: # this is already sorted
            lineNum, charNum = linkLy
            # get that token
            token = parser.tokens(lySrcLines[lineNum - 1][charNum:]).next()

            if isinstance(token, MusicTokenizer.PitchWord):
                # It's a note; if it's silent, remove it and ignore it
                if linkLy in silentNotes:
                    silentNotes.remove(linkLy)
                    continue
                # otherwise get its index in pixels
                xcenter = (coords[0] + coords[2]) / 2
                noteIndex = int(round(xcenter * imageWidth / pageWidth))
                # add that index into indices
                if noteIndex not in indexNoteSourcesInPage:
                    indexNoteSourcesInPage[noteIndex] = []
                indexNoteSourcesInPage[noteIndex].append(linkLy)
            elif token == "~":
                # It's a tie.
                # If next note isn't in silent notes, add it
                nextNote = notesAndTies[notesAndTies.index(linkLy) + 1]
                if nextNote not in silentNotes:
                    silentNotes.append(nextNote)
                # otherwise add next one (after the last silent one (if it's tie of harmony))
                else:
                    lastSilentSrcIndex = notesAndTies.index(silentNotes[-1])
                    srcIndexAfterLastSilent = lastSilentSrcIndex + 1
                    linkLyAfterLastSilent = notesAndTies[srcIndexAfterLastSilent]
                    silentNotes.append(linkLyAfterLastSilent)
            else:
                fatal("didn't know what to do with %s" % repr(token))

        noteIndicesInPage = mergeNearbyIndices(indexNoteSourcesInPage)

        # stores info about this page
        indexNoteSourcesByPage.append(indexNoteSourcesInPage)
        noteIndicesByPage.append(noteIndicesInPage)

        progress("PDF: Page %d/%d has been completed." %
                 (pageNum + 1, len(notePositionsByPage)))

    return indexNoteSourcesByPage, noteIndicesByPage

def mergeNearbyIndices(indexNoteSourcesInPage):
    """
    Merges nearby note indices in the given page.  Any within +/- 10
    pixels of each other get merged into a single index.

    Parameters:
      - indexNoteSourcesInPage:
            a dict mapping each index to a list of (lineNum, colNum)
            tuples in the .ly source corresponding to the notes at
            that index within the page, e.g.

                {
                    123 : [    # index
                        (37, 2), # note at index 123, line 37 col 2
                        (37, 5), # note at index 123, line 37 col 5
                    ],
                    ...
                }

    Returns:
      - a sorted list of all indices in the page, post merge

    indexNoteSourcesInPage is also adjusted according to the merging,
    as a side-effect.
    """
    # gets all indices on one page and sort it
    noteIndicesInPage = indexNoteSourcesInPage.keys()
    noteIndicesInPage.sort()

    # merges indices within +/- 10 pixels of each other
    skipNext = False
    for index in noteIndicesInPage[:-1]:
        if skipNext:
            skipNext = False
            continue
        # gets next index
        nextIndex = noteIndicesInPage[noteIndicesInPage.index(index) + 1]
        if index in xrange(nextIndex - 10, nextIndex + 10):
            # merges them and remove next index
            indexNoteSourcesInPage[index].extend(indexNoteSourcesInPage[nextIndex])
            del indexNoteSourcesInPage[nextIndex]
            noteIndicesInPage.remove(nextIndex)
            skipNext = True

    return noteIndicesInPage

def pitchValue(token, parser):
    """
    Returns the numerical pitch of the token representing a note,
    where the token is treated as an absolute pitch, and each
    increment of 1 is equivalent to going up a semi-tone (half-step).
    This facilitates comparison to MIDI NoteOn events, although
    arithmetic modulo 12 may be required.
    """
    parser.language = 'english'
    p = ly.tools.Pitch.fromToken(token, parser)

    accidentalSemitoneSteps = 2 * p.alter
    if accidentalSemitoneSteps.denominator != 1:
        fatal("Uh-oh, we don't support microtones yet")

    pitch = p.octave * 12 + \
            C_MAJOR_SCALE_STEPS[p.note] + \
            accidentalSemitoneSteps

    return int(pitch)

def alignIndicesWithTicks(indexNoteSourcesByPage, noteIndicesByPage,
                          tokens, parser, midiTicks, notesInTicks):
    """
    Build a list of note indices (grouped by page) which align with
    the ticks in midiTicks, by sequentially comparing the notes at
    each index in the images with the notes at each tick in the MIDI
    stream.

    If MIDI events are found with no corresponding notation (e.g. due
    to notes hidden via \hideNotes), they are skipped and the
    containing tick is removed from midiTicks.  If notes are found in
    the index with no corresponding MIDI event, then currently we flag
    an error.  If this turns out to be a valid use case then we can
    change this behaviour.

    FIXME: there is probably a bug which will be triggered when a
    chord appears on a beat containing no notated notes, but the next
    note index contains one or more notes in the chord.  In this case,
    I would expect the latter to match the MIDI tick containing the
    chord, which would throw synchronization off.  But chords in MIDI
    probably sound lousy and should be turned off:

    http://article.gmane.org/gmane.comp.gnu.lilypond.general/61500

    Parameters:
      - indexNoteSourcesByPage: as returned by getFilteredIndices()
      - noteIndicesByPage:      as returned by getFilteredIndices()
      - tokens:                 as returned by getNotePositions()
      - parser:                 as returned by getNotePositions()
      - notesInTicks:           as returned by getNotesInTicks()
      - midiTicks: a sorted list of which ticks contain NoteOn events.
                   The last tick corresponds to the earliest
                   EndOfTrackEvent found across all MIDI channels.
    Returns:
      - alignedNoteIndicesByPage:
          a list of sorted lists, one per page, containing all the
          indices on that page aligned in order with the MIDI ticks

    Side-effect:
      - entries may be removed from midiTicks (see above)
    """

    alignedNoteIndicesByPage = []

    # index into list of MIDI ticks
    midiIndex = 0

    for pageNum, noteIndicesInPage in enumerate(noteIndicesByPage):
        # final indices of notes on one page
        alignedNoteIndicesInPage = []

        indexNoteSourcesInPage = indexNoteSourcesByPage[pageNum]

        # index into list of note indices
        i = 0

        while i < len(noteIndicesInPage):
            if midiIndex == len(midiTicks):
                fatal("Ran out of MIDI indices after %d. Current PDF index: %d" %
                      (midiIndex, index))

            index = noteIndicesInPage[i]
            indexNoteSources = indexNoteSourcesInPage[index]

            tick = midiTicks[midiIndex]
            events = notesInTicks[tick]

            debug("index %d, tick %d" % (index, tick))

            # Build a dict tracking which MIDI pitches (modulo the
            # octave) are present in the current tick.  Pitches will
            # be removed from this as they match notes in
            # indexNoteSources.
            midiPitches = { }
            for event in events:
                pitch = event.get_pitch() % 12
                midiPitches[pitch] = event

            debug("    midiPitches: %s" % repr(midiPitches))

            # Check every note from the source is in the MIDI tick.
            # If only some are, abort with an error.  If none are, we
            # skip this MIDI tick, assuming it corresponds to a
            # transparent note caused by \hideNotes or similar, or a
            # chord.
            matchCount = 0
            for indexNoteSource in indexNoteSources:
                token = tokens[indexNoteSource]
                notePitch = pitchValue(token, parser) % 12
                if notePitch in midiPitches:
                    matchCount += 1
                    del midiPitches[notePitch]
                    debug("        matched '%s' @ %d:%d to MIDI pitch %d" %
                          (token, indexNoteSource[0], indexNoteSource[1], notePitch))

            if matchCount == 0:
                # No pitches in this index matched this MIDI tick -
                # maybe it was a note hidden by \hideNotes.  So let's
                # skip the tick.
                midiTicks.pop(midiIndex)
                debug("    WARNING: skipping MIDI tick %d; contents:" % tick)
                for event in events:
                    debug("        pitch %d length %d" %
                          (event.get_pitch(), event.length))
                continue

            # Regardless of what we found, we're going to move onto
            # the next tick now.
            midiIndex += 1

            if midiPitches:
                debug("    WARNING: only matched %d/%d MIDI notes "
                      "at index %d tick %d\n" %
                      (matchCount, len(events), index, tick))
                for event in midiPitches.values():
                    debug("        pitch %d length %d" %
                          (event.get_pitch(), event.length))
                continue

            debug("    all pitches matched in this MIDI tick!")
            alignedNoteIndicesInPage.append(index)
            i += 1

        # add indices on one page into final noteIndicesByPage
        alignedNoteIndicesByPage.append(alignedNoteIndicesInPage)

    if midiIndex < len(midiTicks) - 1:
        warn("ran out of notes in PDF at MIDI tick %d (%d/%d ticks)" % \
                 (midiTicks[midiIndex], midiIndex + 1, len(midiTicks)))

    return alignedNoteIndicesByPage

def getNoteIndices(pdfFileName, imageWidth, lySrcFileName, lySrcLines,
                   midiTicks, notesInTicks):
    """
    Returns indices of notes in generated PNG images (through PDF
    file).  A note's index is the x-coordinate of its center in the
    PNG image containing it.  This relies on the fact that the PDF
    file was generated with -dpoint-and-click.

    It iterates through PDF pages:

    - first pass: finds the position in the PDF file and in the *.ly
      code of every note or tie

    - second pass: goes through notePositionsByPage separating notes and
      ties and merging near indices (e.g. 834, 835, 833, ...)

    Then it sequentially compares the indices of the images with
    indices in the MIDI: the first position in the MIDI with the first
    position on the image.  If it's equal, then it's OK.  If not, then
    it skips to the next position on image (see getMidiEvents() and
    notesInTicks).  Then it compares the next image index with MIDI
    index, and so on.

    Returns a list of note indices in the PNG image, grouped by page.

    Params:
    - pdfFileName:      name of generated PDF file (string)
    - imageWidth:       width of PNG file(s)
    - lySrcFileName:    name of .ly file
    - lySrcLines:       loaded *.ly file in memory (list)
    - midiTicks:        all ticks with notes in MIDI file
    - notesInTicks:     how many notes starts in each tick
    """

    notePositionsByPage, notesAndTies, tokens, parser, pageWidth = \
        getNotePositions(pdfFileName, lySrcFileName, lySrcLines)
    indexNoteSourcesByPage, noteIndicesByPage = \
        getFilteredIndices(notePositionsByPage, notesAndTies,
                           lySrcLines, imageWidth, pageWidth)

    return alignIndicesWithTicks(indexNoteSourcesByPage,
                                 noteIndicesByPage, tokens, parser,
                                 midiTicks, notesInTicks)

def genVideoFrames(midiResolution, temposList, midiTicks,
                   width, height, fps,
                   noteIndicesByPage, notesImages, cursorLineColor):
    """
    Generates frames for the final video, synchronized with audio.
    Each frame is written to disk as a PNG file.

    Counts time between starts of two notes, gets their positions on
    image and generates needed amount of frames. The index of last
    note on every page is "doubled", so it waits at the end of page.
    The required number of frames for every pair is computed as a real
    number and because a fractional number of frames can't be
    generated, they are stored in dropFrame and if that is > 1, it
    skips generating one frame.

    Params:
      - midiResolution:    resolution of MIDI file
      - temposList:        list of possible tempos in MIDI
      - midiTicks:         list of ticks with NoteOnEvent
      - width:             pixel width of frames (and video)
      - height:            pixel height of frames (and video)
      - fps:               frame rate of video
      - noteIndicesByPage: indices of notes in pictures
      - notesImages:       names of that images (list of strings)
      - cursorLineColor:   color of middle line
    """

    midiIndex = 0
    tempoIndex = 0
    frameNum = 0

    # folder to store frames for video
    if not os.path.exists("notes"):
        os.mkdir("notes")

    firstTempo = temposList[tempoIndex][1]
    totalFrames = int(round(float(firstTempo) / midiResolution *
                            midiTicks[-1] / 1000000 * fps))
    progress("SYNC: ly2video will generate approx. %d frames." % totalFrames)
    if not DEBUG:
        progress("A dot is displayed for every 10 frames generated.")

    dropFrame = 0.0

    for pageNum, indices in enumerate(noteIndicesByPage):
        # open image of staff
        notesPic = Image.open(notesImages[pageNum])

        # duplicate last index
        indices.append(indices[-1])

        for i in xrange(len(indices) - 1):
            # get two indices of notes (pixels)
            startIndex = indices[i]
            endIndex   = indices[i + 1]

            # get two indices of MIDI events (ticks)
            startTick = midiTicks[midiIndex]
            midiIndex += 1
            endTick = midiTicks[midiIndex]
            ticks = endTick - startTick

            tempoTick, tempo = temposList[tempoIndex]
            # if there's gonna be change in tempo, change it
            if tempoIndex < len(temposList) - 1:
                if startTick == temposList[tempoIndex + 1][0]:
                    tempoIndex += 1
            debug("  need %f frames @ %d fps" %
                  (neededFrameSetSecs * fps, fps))

            # how many frames do I need?
            neededFrames = (float(tempo) / midiResolution
                            * ticks / 1000000 * fps)
            # how many frames can be generated?
            realFrames = int(round(neededFrames))
            # add that difference between needed and real value into dropFrame
            dropFrame += (realFrames - neededFrames)
            # pixel shift for one frame
            shift = (endIndex - startIndex) * 1.0 / neededFrames

            for posun in xrange(realFrames):
                # if I need drop more than "1.0" frames, drop one
                if dropFrame >= 1.0:
                    dropFrame -= 1.0
                    continue
                else:
                    # get frame from image of staff, args - (("left upper corner", "right lower corner"))
                    left = int(startIndex + round(posun * shift)
                                    - (width / 2))
                    right = int(startIndex + round(posun * shift)
                                     + (width / 2))
                    frame = notesPic.copy().crop((left, 0, right, height))
                    # add middle line
                    for pixel in xrange(height):
                        frame.putpixel((width / 2, pixel), cursorLineColor)
                        frame.putpixel(((width / 2) + 1, pixel), cursorLineColor)

                    # save that frame
                    frame.save(tmpPath("notes", "frame%d.png" % frameNum))
                    frameNum += 1
                    if not DEBUG and frameNum % 10 == 0:
                        sys.stdout.write(".")
                        sys.stdout.flush()
        print

        progress("SYNC: Generated %d frames for page %d/%d" %
                 (frameNum, pageNum + 1, len(noteIndicesByPage)))

def genWavFile(timidity, midiPath):
    """
    Call TiMidity++ to convert MIDI to .wav.
    It has a weird problem where it converts any '.' into '_'
    in the input path, so we run it on the file's relative path
    not the absolute path.
    """
    progress("Running TiMidity++ on %s to generate .wav audio ..." % midiPath)
    dirname, midiFile = os.path.split(midiPath)
    os.chdir(dirname)
    cmd = [timidity, midiFile, "-Ow"]
    progress(safeRun(cmd, exitcode=11))
    wavExpected = midiPath.replace('.midi', '.wav')
    if not os.path.exists(wavExpected):
        fatal("TiMidity++ failed to generate %s ?!" % wavExpected)
    return wavExpected

def generateSilence(length):
    """
    Generates silent audio for the title screen.

    author: Mister Muffin,
    http://blog.mister-muffin.de/2011/06/04/generate-silent-wav/

    Params:
    - length: length of that silence
    """

    #
    channels = 2    # number of channels
    bps = 16        # bits per sample
    sample = 44100  # sample rate
    ExtraParamSize = 0
    Subchunk1Size = 16 + 2 + ExtraParamSize
    Subchunk2Size = length * sample * channels * bps/8
    ChunkSize = 4 + (8 + Subchunk1Size) + (8 + Subchunk2Size)

    fSilence = open("silence.wav", "w")

    fSilence.write("".join([
        'RIFF',                                # ChunkID (magic)      # 0x00
        pack('<I', ChunkSize),                 # ChunkSize            # 0x04
        'WAVE',                                # Format               # 0x08
        'fmt ',                                # Subchunk1ID          # 0x0c
        pack('<I', Subchunk1Size),             # Subchunk1Size        # 0x10
        pack('<H', 1),                         # AudioFormat (1=PCM)  # 0x14
        pack('<H', channels),                  # NumChannels          # 0x16
        pack('<I', sample),                    # SampleRate           # 0x18
        pack('<I', bps/8 * channels * sample), # ByteRate             # 0x1c
        pack('<H', bps/8 * channels),          # BlockAlign           # 0x20
        pack('<H', bps),                       # BitsPerSample        # 0x22
        pack('<H', ExtraParamSize),            # ExtraParamSize       # 0x22
        'data',                                # Subchunk2ID          # 0x24
        pack('<I', Subchunk2Size),             # Subchunk2Size        # 0x28
        '\0'*Subchunk2Size
    ]))
    fSilence.close()
    return "silence.wav"

def output_divider_line():
    progress(60 * "-")

def debug(text):
    if DEBUG:
        print text

def progress(text):
    print text

def warn(text):
    sys.stderr.write("WARNING: " + text)

def fatal(text, status=1):
    sys.stderr.write("ERROR: " + text)
    sys.exit(status)

def tmpPath(*dirs):
    segments = [ 'ly2video.tmp' ]
    segments.extend(dirs)
    return os.path.join(runDir, *segments)

def parseOptions():
    parser = OptionParser("usage: %prog [options]")

    parser.add_option("-i", "--input", dest="input",
                      help="input LilyPond file", metavar="FILE")
    parser.add_option("-o", "--output", dest="output",
                      help='name of output video (e.g. "myNotes.avi", default is input + .avi)',
                      metavar="FILE")
    parser.add_option("-b", "--beatmap", dest="beatmap",
                      help='name of beatmap file for adjusting MIDI tempo',
                      metavar="FILE")
    parser.add_option("-c", "--color", dest="color",
                      help='name of color of middle bar (default is "red")', metavar="COLOR",
                      default="red")
    parser.add_option("-f", "--fps", dest="fps",
                      help='frame rate of final video (default is "30")', type="int", metavar="FPS",
                      default=30)
    parser.add_option("-x", "--width", dest="width",
                      help='pixel width of final video (default is 1280)',
                      metavar="HEIGHT", type="int", default=1280)
    parser.add_option("-y", "--height", dest="height",
                      help='pixel height of final video (default is 720)',
                      metavar="HEIGHT", type="int", default=720)
    parser.add_option("--title-at-start", dest="titleAtStart",
                      help='adds title screen at the start of video (with name of song and its author)',
                      action="store_true", default=False)
    parser.add_option("--title-delay", dest="titleDelay",
                      help='time to display the title screen (default is "3" seconds)', type="int",
                      metavar="SECONDS", default=3)
    parser.add_option("--windows-ffmpeg", dest="winFfmpeg",
                      help='(for Windows users) folder with ffpeg.exe (e.g. "C:\\ffmpeg\\bin\\")',
                      metavar="PATH", default="")
    parser.add_option("--windows-timidity", dest="winTimidity",
                      help='(for Windows users) folder with timidity.exe (e.g. "C:\\timidity\\")',
                      metavar="PATH", default="")
    parser.add_option("-d", "--debug", dest="debug",
                      help="don't remove temporary working files",
                      action="store_true", default=False)
    parser.add_option("-k", "--keep", dest="keepTempFiles",
                      help="don't remove temporary working files",
                      action="store_true", default=False)

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    options, args = parser.parse_args()

    if options.debug:
        global DEBUG
        DEBUG = True

    return options, args

def portableDevNull():
    if sys.platform.startswith("linux"):
        return "/dev/null"
    elif sys.platform.startswith("win"):
        return "NUL"

def applyBeatmap(src, dst, beatmap):
    prog = "midi-rubato"
    cmd = [prog, src, dst, beatmap]
    progress("Applying beatmap via '%s'" % " ".join(cmd))
    progress(safeRun(cmd))

def safeRun(cmd, errormsg=None, exitcode=None, shell=False):
    try:
        stdout = subprocess.check_output(cmd, shell=shell)
    except:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        excmsg = "%s: %s" % (exc_type.__name__, exc_value)
        for i in xrange(len(cmd)):
            if cmd[i].find(' ') != -1 or cmd[i].find('"') != -1:
                cmd[i] = '"' + cmd[i].replace('"', '\"') + '"'
        if errormsg is None:
            errormsg = "Failed to run command: %s: %s" % \
                (" ".join(cmd), excmsg)
        fatal(errormsg, exitcode)

    return stdout

def findExecutableDependencies(options):
    stdout = safeRun(["lilypond", "-v"], "LilyPond was not found.", 1)
    progress("LilyPond was found.")
    m = re.search('\AGNU LilyPond (\d[\d.]+\d)', stdout)
    if not m:
        fatal("Couldn't determine LilyPond version via lilypond -v")
    version = m.group(1)

    redirectToNull = " >%s" % portableDevNull()

    ffmpeg = options.winFfmpeg + "ffmpeg"
    if os.system(ffmpeg + " -version" + redirectToNull) != 0:
        fatal("FFmpeg was not found (maybe use --windows-ffmpeg?).", 2)
    progress("FFmpeg was found.")

    timidity = options.winTimidity + "timidity"
    if os.system(timidity + " -v" + redirectToNull) != 0:
        fatal("TiMidity++ was not found (maybe use --windows-timidity?).", 3)
    progress("TiMidity++ was found.")

    output_divider_line()

    return version, ffmpeg, timidity

def getCursorLineColor(options):
    options.color = options.color.lower()
    if options.color == "black":
        return (0,0,0)
    elif options.color == "yellow":
        return (255,255,0)
    elif options.color == "red":
        return (255,0,0)
    elif options.color == "green":
        return (0,128,0)
    elif options.color == "blue":
        return (0,0,255)
    elif options.color == "brown":
        return (165,42,42)
    else:
        warn("Color was not found, ly2video will use default one ('red').")
        return (255,0,0)

def absPathFromRunDir(path):
    if os.path.isabs(path):
        return path
    return os.path.join(runDir, path)

def getOutputFile(options):
    outputFile = options.output
    if outputFile is None:
        basename, ext = os.path.splitext(options.input)
        outputFile = basename + '.avi'
    return absPathFromRunDir(outputFile)

def callFfmpeg(ffmpeg, options, wavPath, outputFile):
    fps = str(options.fps)
    framePath = tmpPath('notes', 'frame%d.png')

    if not options.titleAtStart:
        cmd = [
            ffmpeg,
            "-f", "image2",
            "-r", fps,
            "-i", framePath,
            "-i", wavPath,
            outputFile
        ]
        safeRun(cmd, exitcode=13)
    else:
        # generate silent title video
        silentAudio = generateSilence(titleLength)
        titlePath   = tmpPath("title.mpg")
        cmd = [
            ffmpeg,
            "-f", "image2",
            "-r", fps,
            "-i", framePath,
            "-i", silentAudio,
            "-same_quant",
            titlePath
        ]
        safeRun(cmd, exitcode=14)

        # generate video with notes
        notesPath = tmpPath("notes.mpg")
        cmd = [
            ffmpeg,
            "-f", "image2",
            "-r", fps,
            "-i", framePath,
            "-i", wavPath,
            "-same_quant",
            notesPath
        ]
        safeRun(cmd, exitcode=15)

        # join the files
        joinedPath = tmpPath('joined.mpg')
        if sys.platform.startswith("linux"):
            safeRun("cat '%s' '%s' > %s" % (titlePath, notesPath, joinedPath), shell=True)
        elif sys.platform.startswith("win"):
            os.system('copy "%s" /B + "%s" /B "%s" /B' % (titlePath, notesPath, joinedPath))

        # create output file
        cmd = [
            ffmpeg,
            "-i", joinedPath,
            outputFile
        ]
        safeRun(cmd, exitcode=16)

def getLyVersion(fileName):
    # if I don't have input file, end
    if fileName == None:
        fatal("LilyPond input file was not specified.", 4)
    else:
        # otherwise try to open fileName
        try:
            fLyFile = open(fileName, "r")
        except IOError:
            fatal("Couldn't read %s" % fileName, 5)

    # find version of LilyPond in .ly input file
    version = ""
    for line in fLyFile.readlines():
        if line.find("\\version") != -1:
            parser = Tokenizer()
            for token in parser.tokens(line):
                if token.__class__.__name__ == "StringQuoted":
                    version = str(token)[1:-1]
                    break
            if version != "":
                break
    fLyFile.close()

    return version

def getNoteImages():
    """
    Returns a sorted list of the generated PNG files.
    """
    notesImages = []
    for fileName in os.listdir(tmpPath()):
        m = re.search('(?:.*/)?sanitised(?:-page(\d+))?\.png$', fileName)
        if m:
            progress("Found generated image: %s" % fileName)
            if m.group(1):
                i = int(m.group(1))
            else:
                i = 1
            newFileName = "sanitised-page%04d.png" % i
            newPath = tmpPath(newFileName)

            if newFileName != fileName:
                os.rename(fileName, newPath)
                progress("  renamed -> %s" % newFileName)
            notesImages.append(newPath)
    notesImages.sort()
    return notesImages

def getImageWidth(notesImages):
    """
    Get width of first image in pixels (we assume they all have the
    same width).  This will allow us to convert PDF coordinates into
    dimensions measured in pixels.
    """
    tmpImage = Image.open(notesImages[0])
    picWidth = tmpImage.size[0]
    progress("Width of %s is %d pixels" % (notesImages[0], picWidth))
    del tmpImage
    return picWidth

def getNumStaffLines(lyFile):
    # generate preview of notes
    cmd = [
        "lilypond",
        "-dpreview",
        "-dprint-pages=#f",
        lyFile
    ]
    progress("Generating preview from %s ..." % lyFile)
    output_divider_line()
    os.chdir(tmpPath())
    safeRun(cmd, exitcode=7)
    output_divider_line()
    progress("Generated preview from %s" % lyFile)

    # move generated files into temporary directory
    dirname, filename = os.path.split(lyFile)
    if dirname != tmpPath():
        basename, suffix = os.path.splitext(filename)
        for ext in ('png', 'eps', 'pdf'):
            generated = basename + '.' + ext
            src = os.path.join(dirname, generated)
            dst = tmpPath(generated)
            os.rename(src, dst)
            progress("Moved %s to %s" % (src, dst))

    # find preview image and get num of staff lines
    previewPic = ""
    previewFilesTmp = os.listdir(".")
    previewFiles = []
    for fileName in previewFilesTmp:
        if "preview" in fileName:
            previewFiles.append(fileName)
            if fileName.split(".")[-1] == "png":
                previewPic = fileName
    numStaffLines = len(lineIndices(previewPic, 50))

    progress("Found %d staff lines" % numStaffLines)
    return numStaffLines

def sanitiseLy(lyFile, width, height, numStaffLines, titleText, lilypondVersion):
    fLyFile = open(lyFile, "r")

    sanitisedLyFileName = tmpPath("sanitised.ly")

    # create own ly lyFile
    fSanitisedLyFile = open(sanitisedLyFileName, "w")

    # if I add own paper block
    paperBlock = False

    # stores info about header and paper block (and brackets in them)
    headerPart = False
    bracketsHeader = 0
    paperPart = False
    bracketsPaper = 0

    line = fLyFile.readline()
    while line != "":
        # if the line is done
        done = False

        if line.find("\\partial") != -1:
            warn('Ly2video has found "\\partial" command ' +
                 "in your lyFile.  This could cause problems.")

        # ignore these commands
        if (line.find("\\include \"articulate.ly\"") != -1
            or line.find("\\pointAndClickOff") != -1
            or line.find("#(set-global-staff-size") != -1
            or line.find("\\bookOutputName") != -1):
            line = fLyFile.readline()

        # if I find version, write own paper block right behind it
        if line.find("\\version") != -1:
            done = True
            fSanitisedLyFile.write(line)
            writePaperHeader(fSanitisedLyFile, width, height, numStaffLines, lilypondVersion)
            paperBlock = True

        # get needed info from header block and ignore it
        if (line.find("\\header") != -1 or headerPart) and not done:
            if line.find("\\header") != -1:
                fSanitisedLyFile.write("\\header {\n   tagline = ##f composer = ##f\n}\n")
                headerPart = True

            done = True

            if line.find("title = ") != -1:
                titleText.name = line.split("=")[-1].strip()[1:-1]
            if line.find("composer = ") != -1:
                titleText.author = line.split("=")[-1].strip()[1:-1]

            for znak in line:
                if znak == "{":
                    bracketsHeader += 1
                elif znak == "}":
                    bracketsHeader -= 1
            if bracketsHeader == 0:
                headerPart = False

        # ignore paper block
        if (line.find("\\paper") != -1 or paperPart) and not done:
            if line.find("\\paper") != -1:
                paperPart = True

            done = True

            for znak in line:
                if znak == "{":
                    bracketsPaper += 1
                elif znak == "}":
                    bracketsPaper -= 1
            if bracketsPaper == 0:
                paperPart = False

        # add unfoldRepeats right after start of score block
        if (line.find("\\score {") != -1) and not done:
            done = True
            fSanitisedLyFile.write(line + " \\unfoldRepeats\n")

        # parse other lines, ignore page breaking commands and articulate
        if not headerPart and not paperPart and not done:
            finalLine = ""

            if line.find("\\break") != -1:
                finalLine = (line[:line.find("\\break")]
                             + line[line.find("\\break") + len("\\break"):])
            elif line.find("\\noBreak") != -1:
                finalLine = (line[:line.find("\\noBreak")]
                             + line[line.find("\\noBreak") + len("\\noBreak"):])
            elif line.find("\\pageBreak") != -1:
                finalLine = (line[:line.find("\\pageBreak")]
                             + line[line.find("\\pageBreak") + len("\\pageBreak"):])
            elif line.find("\\articulate") != -1:
                finalLine = (line[:line.find("\\articulate")]
                             + line[line.find("\\articulate") + len("\\articulate"):])
            else:
                finalLine = line

            fSanitisedLyFile.write(finalLine)

        line = fLyFile.readline()

    fLyFile.close()

    # if I didn't find \version, write own paper block
    if not paperBlock:
        writePaperHeader(fSanitisedLyFile, width, height, numStaffLines)

    fSanitisedLyFile.close()
    progress("Wrote sanitised version of %s into %s" % (lyFile, sanitisedLyFileName))

    return sanitisedLyFileName

def main():
    """
    Main function of ly2video script.

    It performs the following steps:

    - use Lilypond to generate PNG images, PDF, and MIDI files of the
      music

    - find the spacial and temporal position of each note in the PDF
      and MIDI files respectively

    - combine the positions together to generate the required number
      of video frames

    - create a video file from the individual frames
    """
    (options, args) = parseOptions()

    lilypondVersion, ffmpeg, timidity = findExecutableDependencies(options)

    # title and all about it
    if options.titleAtStart:
        titleLength = options.titleDelay
    else:
        titleLength = 0
    titleText = collections.namedtuple("titleText", "name author")
    titleText.name = "<name of song>"
    titleText.author = "<author>"

    # FIXME.  Ugh, eventually this will be an instance method, and
    # we'll have somewhere nice to save state.
    global runDir
    runDir = os.getcwd()

    # Delete old temporary files.
    if os.path.isdir(tmpPath()):
        shutil.rmtree(tmpPath())
    os.mkdir(tmpPath())

    # .ly input file from user (string)
    lyFile = options.input

    # if it's not 2.14.2, try to convert it
    versionConversion = False
    if getLyVersion(lyFile) != "2.14.2":
        newLyFile = tmpPath('converted.ly')
        if os.system("convert-ly '%s' > '%s'" % (lyFile, newLyFile)) == 0:
            lyFile = newLyFile
            versionConversion = True
        else:
            warn("Convert of input file has failed. " +
                 "This could cause some problems.")
            newLyFile = tmpPath('unconverted.ly')
            os.copy(lyFile, newLyFile)
            lyFile = newLyFile
            output_divider_line()

    numStaffLines = getNumStaffLines(lyFile)

    sanitisedLyFileName = \
        sanitiseLy(lyFile, options.width, options.height,
                   numStaffLines, titleText, lilypondVersion)

    # load .ly file into memory
    fLyFile = open(sanitisedLyFileName, "r")
    lySrcLines = []
    for line in fLyFile.readlines():
        lySrcLines.append(line)
    fLyFile.close()

    progress("Generating PDF, PNG and MIDI files ...")
    os.chdir(tmpPath())
    cmd = [
        "lilypond",
        "-fpdf",
        "--png",
        "-dpoint-and-click",
        "-dmidi-extension=midi",
        sanitisedLyFileName
    ]
    output_divider_line()
    safeRun(cmd, exitcode=9)
    output_divider_line()
    progress("Generated PDF, PNG and MIDI files")

    notesImages = getNoteImages()
    picWidth = getImageWidth(notesImages)

    midiPath = tmpPath("sanitised.midi")
    if options.beatmap:
        newMidiPath = tmpPath("sanitised-adjusted.midi")
        applyBeatmap(midiPath, newMidiPath,
                     absPathFromRunDir(options.beatmap))
        midiPath = newMidiPath

    # find needed data in MIDI
    try:
        midiResolution, temposList, notesInTicks, midiTicks = \
            getMidiEvents(midiPath)
    except Exception as err:
        fatal("MIDI: %s " % err, 10)

    output_divider_line()

    # find notes indices
    noteIndicesByPage = getNoteIndices(tmpPath("sanitised.pdf"),
                                       picWidth,
                                       sanitisedLyFileName, lySrcLines,
                                       midiTicks, notesInTicks)
    output_divider_line()

    # frame rate of output video
    fps = options.fps

    # generate title screen
    if options.titleAtStart:
        generateTitle(titleText, width, height, fps, titleLength)
        output_divider_line()

    # generate notes
    genVideoFrames(midiResolution, temposList, midiTicks,
                   options.width, options.height, fps,
                   noteIndicesByPage, notesImages,
                   getCursorLineColor(options))

    output_divider_line()

    wavPath = genWavFile(timidity, midiPath)

    output_divider_line()

    outputFile = getOutputFile(options)
    callFfmpeg(ffmpeg, options, wavPath, outputFile)

    output_divider_line()

    if options.keepTempFiles:
        progress("Left temporary files in %s" % tmpPath())
    else:
        shutil.rmtree(tmpPath())

    # end
    progress("Ly2video has ended. Your generated file: " + outputFile + ".")
    return 0

if __name__ == '__main__':
    status = main()
    sys.exit(status)
