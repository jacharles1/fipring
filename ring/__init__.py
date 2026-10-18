"""Direct Bluetooth client for the Oura Ring (Gen 3 / Ring 4 / Ring 5).

The protocol knowledge here is a Python port of open_oura
(https://github.com/Th0rgal/open_oura, MIT License, (c) 2026 Thomas Marchand),
which reverse-engineered it from the Android app's native libraries and live
captures. Byte layouts and test vectors are taken from that project.
"""
