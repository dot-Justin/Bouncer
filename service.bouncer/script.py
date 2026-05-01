import sys

from resources.lib.manager import run


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    run(action)
