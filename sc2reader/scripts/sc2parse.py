#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
This script is intended for use debugging parse issues on replays.

    sc2parse [FILES..]

Recursively parses all the files. When exceptions are thrown it catches them
and attempts to print out any information that might be needed for debug. If
information you need isn't available feel free to add new handlers or add to
existing exception handlers.

Also sets logging to INFO so that missing unit type and ability type messages
are caught and reported. At some point these things should be moved to WARN.

If there are parse exceptions, this script should be run to generate an info
for the ticket filed.
"""

import argparse
import sys
import sc2reader
import traceback

sc2reader.log_utils.log_to_console('INFO')

def main():
    parser = argparse.ArgumentParser(description="Recursively parses replay files, inteded for debugging parse issues.")
    parser.add_argument('--one_each', help="Attempt to parse only one Ladder replay for each release_string", action="store_true")
    parser.add_argument('--ladder_only', help="If a non-ladder game fails, ignore it", action="store_true")
    parser.add_argument('folders', metavar='folder', type=str, nargs='+', help="Path to a folder")
    args = parser.parse_args()

    releases_parsed = set()
    for folder in args.folders:
        print "dealing with {}".format(folder)
        for path in sc2reader.utils.get_files(folder,extension='SC2Replay'):
            try:
                rs = sc2reader.load_replay(path, load_level=0).release_string
                already_did = rs in releases_parsed
                releases_parsed.add(rs)
                if not args.one_each or not already_did:
                    replay = sc2reader.load_replay(path, debug=True, load_level=1)
                    if not args.one_each or replay.is_ladder:
                        replay = sc2reader.load_replay(path, debug=True)
                        print 'No problems with {build} - {real_type} on {map_name} - Played {start_time}'.format(**replay.__dict__)
            except sc2reader.exceptions.ReadError as e:
                if args.ladder_only and not e.replay.is_ladder: continue

                print
                print path
                print '{build} - {real_type} on {map_name} - Played {start_time}'.format(**e.replay.__dict__)
                print '[ERROR]', e.message
                for event in e.game_events[-5:]:
                    print '{0} - {1}'.format(hex(event.type),event.bytes.encode('hex'))
                e.buffer.seek(e.location)
                print e.buffer.peek(50).encode('hex')
                print
            except Exception as e:
                print
                print path
                try:
                    replay = sc2reader.load_replay(path, debug=True, load_level=2)
                    print '{build} - {real_type} on {map_name} - Played {start_time}'.format(**replay.__dict__)
                    print '[ERROR]', e.message
                    for pid, attributes in replay.attributes.items():
                        print pid, attributes
                    for pid, info in enumerate(replay.raw_data['replay.details'].players):
                        print pid, info
                    for message in replay.raw_data['replay.message.events'].messages:
                        print message.pid, message.text
                    print replay.raw_data['replay.initData'].player_names
                    traceback.print_exc()
                    print
                except Exception as e2:
                    replay = sc2reader.load_replay(path, debug=True, load_level=0)
                    print 'Total failure parsing {release_string}'.format(**replay.__dict__)
                    print '[ERROR]', e.message
                    print '[ERROR]', e2.message
                    traceback.print_exc()
                    print



if __name__ == '__main__':
    main()
