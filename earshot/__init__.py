"""earshot — the listening half of Plate 197.

Capture from a USB microphone, classify with BirdNET v2.4, write what it
heard to SQLite, and serve the result to the page on the panel.

Everything runs on the Pi. There is no server and no second machine, so
every part of this has to fail quietly and come back on its own.
"""

__version__ = "0.1.0"
