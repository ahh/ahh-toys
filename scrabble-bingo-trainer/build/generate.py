#!/usr/bin/env python3
"""Generate the word-data files the trainer ships with.

Reads the public-domain ENABLE word list (enable1.txt, in this directory) and
emits two data files under ../data:

    words7.js   window.WORDS7 = "aahing\naahs...";   (all 7-letter words)
    words8.js   window.WORDS8 = "...";               (all 8-letter words)

We ship the raw word lists (newline-joined) and do all indexing --- alphagram
grouping, probability ordering --- in the browser at load time. That keeps the
data files tiny and the "probability of drawing this rack" logic in one place
(app.js) where the app can tune it.

Only [a-z] words of the right length are kept.
"""
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "enable1.txt")
OUT = os.path.join(HERE, "..", "data")
# Public-domain ENABLE word list (not committed -- fetched on demand).
SRC_URL = "https://raw.githubusercontent.com/dolph/dictionary/master/enable1.txt"

WORD_RE = re.compile(r"^[a-z]+$")


def ensure_source():
    if not os.path.exists(SRC):
        print(f"downloading word list from {SRC_URL} ...")
        urllib.request.urlretrieve(SRC_URL, SRC)


def load(length):
    words = []
    with open(SRC, encoding="utf-8") as fh:
        for line in fh:
            w = line.strip()
            if len(w) == length and WORD_RE.match(w):
                words.append(w)
    words.sort()
    return words


def emit(length, varname, filename):
    words = load(length)
    body = "\\n".join(words)
    path = os.path.join(OUT, filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"window.{varname} = \"{body}\";\n")
    size = os.path.getsize(path)
    print(f"{filename}: {len(words):>6} words  {size/1024:6.1f} KB")


if __name__ == "__main__":
    ensure_source()
    os.makedirs(OUT, exist_ok=True)
    emit(7, "WORDS7", "words7.js")
    emit(8, "WORDS8", "words8.js")
