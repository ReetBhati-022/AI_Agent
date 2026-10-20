"""Safe recursive walker: skips excluded dirs and links/junctions, tolerates errors, honours a deadline."""
import os
import time

from security import permissions


class Walker:
    def __init__(self, root: str, deadline: float):
        self.root = root
        self.deadline = deadline
        self.skipped_dirs = 0
        self.timed_out = False

    def files(self, include_dirs=False):
        stack = [self.root]
        counter = 0
        while stack:
            if time.monotonic() > self.deadline:
                self.timed_out = True
                return
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        counter += 1
                        if counter % 2000 == 0 and time.monotonic() > self.deadline:
                            self.timed_out = True
                            return
                        try:
                            if permissions.entry_is_link(entry):
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if not permissions.is_excluded(entry.path):
                                    stack.append(entry.path)
                                    if include_dirs:
                                        yield entry
                            elif entry.is_file(follow_symlinks=False):
                                yield entry
                        except OSError:
                            continue
            except OSError:
                self.skipped_dirs += 1