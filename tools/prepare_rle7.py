"""Entry point for the Windows rle7 firmware generator."""
# SPDX-License-Identifier: GPL-3.0-only

from prepare_firmware import main


if __name__ == '__main__':
    raise SystemExit(main(default_revision='rle7'))
