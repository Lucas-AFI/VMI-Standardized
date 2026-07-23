"""
VMI Update Process - Item Image Sync (Task Scheduler entry point)

Existing Task Scheduler entries across client machines already call this
file directly by name (Program: python, Arguments: matrix_image_save.py,
Start in: C:\\update_process), from before image sync was folded into the
standardized repo. Kept as a thin, no-argument wrapper specifically so none
of those Task Scheduler entries need to change on any machine.

The actual logic lives in images.sync_images() -- `python main.py -a images`
calls the exact same function, so there is no behavior difference between
the two entry points. Use main.py's for any newly-provisioned machine;
this file exists purely for backward compatibility with what's already
scheduled everywhere else.

Usage:
    python matrix_image_save.py
"""

from log import configure_logs
from images import sync_images

if __name__ == '__main__':
    configure_logs()
    sync_images()
