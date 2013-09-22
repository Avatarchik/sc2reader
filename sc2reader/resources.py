    # -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, unicode_literals, division

from collections import defaultdict, namedtuple
from datetime import datetime
import hashlib
import sys
from xml.etree import ElementTree
import zlib

import mpyq
import sc2reader
from sc2reader import utils
from sc2reader.decoders import BitPackedDecoder
from sc2reader import log_utils
from sc2reader import readers
from sc2reader import exceptions
from sc2reader.data import builds as datapacks
from sc2reader.exceptions import SC2ReaderLocalizationError
from sc2reader.objects import Participant, Observer, Computer, Team, PlayerSummary, Graph, BuildEntry, MapInfo
from sc2reader.constants import REGIONS, GAME_SPEED_FACTOR, LOBBY_PROPERTIES


class Resource(object):
    def __init__(self, file_object, filename=None, factory=None, **options):
        self.factory = factory
        self.opt = utils.AttributeDict(options)
        self.logger = log_utils.get_logger(self.__class__)
        self.filename = filename or getattr(file_object, 'name', 'Unavailable')

        if hasattr(file_object, 'seek'):
            file_object.seek(0)
            self.filehash = hashlib.sha256(file_object.read()).hexdigest()
            file_object.seek(0)


class Replay(Resource):

    #: A nested dictionary of player => { attr_name : attr_value } for
    #: known attributes. Player 16 represents the global context and
    #: contains attributes like game speed.
    attributes = defaultdict(dict)

    #: Fully qualified filename of the replay file represented.
    filename = str()

    #: Total number of frames in this game at 16 frames per second.
    frames = int()

    #: The SCII client build number
    build = int()

    #: The SCII game engine build number
    base_build = int()

    #: The full version release string as seen on Battle.net
    release_string = str()

    #: A tuple of the individual pieces of the release string
    versions = tuple()

    #: The game speed: Slower, Slow, Normal, Fast, Faster
    speed = str()

    #: Deprecated, use :attr:`game_type` or :attr:`real_type` instead
    type = str()

    #: The game type choosen at game creation: 1v1, 2v2, 3v3, 4v4, FFA
    game_type = str()

    #: The real type of the replay as observed by counting players on teams.
    #: For outmatched games, the smaller team numbers come first.
    #: Example Values: 1v1, 2v2, 3v3, FFA, 2v4, etc.
    real_type = str()

    #: The category of the game, Ladder and Private
    category = str()

    #: A flag for public ladder games
    is_ladder = bool()

    #: A flag for private non-ladder games
    is_private = bool()

    #: The raw hash name of the s2ma resource as hosted on bnet depots
    map_hash = str()

    #: The name of the map the game was played on
    map_name = str()

    #: A reference to the loaded :class:`Map` resource.
    map = None

    #: The UTC time (according to the client NOT the server) that the game
    #: was ended as represented by the Windows OS
    windows_timestamp = int()

    #: The UTC time (according to the client NOT the server) that the game
    #: was ended as represented by the Unix OS
    unix_timestamp = int()

    #: The time zone adjustment for the time zone registered on the local
    #: computer that recorded this replay.
    time_zone = int()

    #: Deprecated: See `end_time` below.
    date = None

    #: A datetime object representing the utc time at the end of the game.
    end_time = None

    #: A datetime object representing the utc time at the start of the game
    start_time = None

    #: Deprecated: See `game_length` below.
    length = None

    #: The :class:`Length` of the replay as an alternative to :attr:`frames`
    game_length = None

    #: The :class:`Length` of the replay in real time adjusted for the game speed
    real_length = None

    #: The gateway the game was played on: us, eu, sea, etc
    gateway = str()

    #: An integrated list of all the game events
    events = list()

    #: A list of :class:`Team` objects from the game
    teams = list()

    #: A dict mapping team number to :class:`Team` object
    team = dict()

    #: A list of :class:`Player` objects from the game
    players = list()

    #: A dual key dict mapping player names and numbers to
    #: :class:`Player` objects
    player = utils.PersonDict()

    #: A list of :class:`Observer` objects from the game
    observers = list()

    #: A list of :class:`Person` objects from the game representing
    #: both the player and observer lists
    people = list()

    #: A dual key dict mapping :class:`Person` object to their
    #: person id's and names
    person = utils.PersonDict()

    #: A list of :class:`Person` objects from the game representing
    #: only the human players from the :attr:`people` list
    humans = list()

    #: A list of :class:`Computer` objects from the game.
    computers = list()

    #: A list of all the chat message events from the game
    messages = list()

    #: A list of pings sent by all the different people in the game
    pings = list()

    #: A list of packets sent between the various game clients
    packets = list()

    #: A reference to the :class:`Person` that recorded the game
    recorder = None

    #: If there is a valid winning team this will contain a :class:`Team` otherwise it will be :class:`None`
    winner = None

    #: A dictionary mapping unit unique ids to their corresponding classes
    objects = dict()

    #: A sha256 hash uniquely representing the combination of people in the game.
    #: Can be used in conjunction with date times to match different replays
    #: of the game game.
    people_hash = str()

    #: SC2 Expansion. One of 'WoL', 'HotS'
    expasion = str()

    #: True of the game was resumed from a replay
    resume_from_replay = False

    #: A flag marking which method was used to resume from replay. Unknown interpretation.
    resume_method = None

    #: Lists info for each user that is resuming from replay.
    resume_user_info = None


    def __init__(self, replay_file, filename=None, load_level=4, engine=sc2reader.engine, **options):
        super(Replay, self).__init__(replay_file, filename, **options)
        self.datapack = None
        self.raw_data = dict()

        # The current load level of the replay
        self.load_level = None

        #default values, filled in during file read
        self.player_names = list()
        self.other_people = set()
        self.speed = ""
        self.type = ""
        self.game_type = ""
        self.real_type = ""
        self.category = ""
        self.is_ladder = False
        self.is_private = False
        self.map = None
        self.map_hash = ""
        self.gateway = ""
        self.events = list()
        self.events_by_type = defaultdict(list)
        self.teams, self.team = list(), dict()

        self.player = utils.PersonDict()
        self.observer = utils.PersonDict()
        self.human = utils.PersonDict()
        self.computer = utils.PersonDict()
        self.entity = utils.PersonDict()

        self.players = list()
        self.observers = list()  # Unordered list of Observer
        self.humans = list()
        self.computers = list()
        self.entities = list()

        self.attributes = defaultdict(dict)
        self.messages = list()
        self.recorder = None  # Player object
        self.packets = list()
        self.objects = {}
        self.active_units = {}
        self.game_fps = 16.0

        self.tracker_events = list()
        self.game_events = list()

        # Bootstrap the readers.
        self.registered_readers = defaultdict(list)
        self.register_default_readers()

        # Bootstrap the datapacks.
        self.registered_datapacks = list()
        self.register_default_datapacks()

        # Unpack the MPQ and read header data if requested
        # Since the underlying traceback isn't important to most people, don't expose it in python2 anymore
        if load_level >= 0:
            self.load_level = 0
            try:
                self.archive = mpyq.MPQArchive(replay_file, listfile=False)
            except Exception as e:
                raise exceptions.MPQError("Unable to construct the MPQArchive", e)

            header_content = self.archive.header['user_data_header']['content']
            header_data = BitPackedDecoder(header_content).read_struct()
            self.versions = list(header_data[1].values())
            self.frames = header_data[3]
            self.build = self.versions[4]
            self.base_build = self.versions[5]
            self.release_string = "{0}.{1}.{2}.{3}".format(*self.versions[1:5])
            self.game_length = utils.Length(seconds=self.frames/16)
            self.length = self.real_length = utils.Length(seconds=int(self.frames/self.game_fps))

        # Load basic details if requested
        if load_level >= 1:
            self.load_level = 1
            for data_file in ['replay.initData', 'replay.details', 'replay.attributes.events']:
                self._read_data(data_file, self._get_reader(data_file))
            self.load_details()
            self.datapack = self._get_datapack()

            # Can only be effective if map data has been loaded
            if options.get('load_map', False):
                self.load_map()

        # Load players if requested
        if load_level >= 2:
            self.load_level = 2
            for data_file in ['replay.message.events']:
                self._read_data(data_file, self._get_reader(data_file))
            self.load_message_events()
            self.load_players()

        # Load tracker events if requested
        if load_level >= 3:
            self.load_level = 3
            for data_file in ['replay.tracker.events']:
                self._read_data(data_file, self._get_reader(data_file))
            self.load_tracker_events()


        # Load events if requested
        if load_level >= 4:
            self.load_level = 4
            for data_file in ['replay.game.events']:
                self._read_data(data_file, self._get_reader(data_file))
            self.load_game_events()

        # Run this replay through the engine as indicated
        if engine:
            engine.run(self)

    def load_details(self):
        if 'replay.attributes.events' in self.raw_data:
            # Organize the attribute data to be useful
            self.attributes = defaultdict(dict)
            attributesEvents = self.raw_data['replay.attributes.events']
            for attr in attributesEvents:
                self.attributes[attr.player][attr.name] = attr.value

            # Populate replay with attributes
            self.speed = self.attributes[16]['Game Speed']
            self.category = self.attributes[16]['Game Mode']
            self.type = self.game_type = self.attributes[16]['Teams']
            self.is_ladder = (self.category == "Ladder")
            self.is_private = (self.category == "Private")

        if 'replay.details' in self.raw_data:
            details = self.raw_data['replay.details']

            self.map_name = details['map_name']

            self.gateway = details['cache_handles'][0].server.lower()
            self.map_hash = details['cache_handles'][-1].hash
            self.map_file = details['cache_handles'][-1]

            #Expand this special case mapping
            if self.gateway == 'sg':
                self.gateway = 'sea'

            dependency_hashes = [d.hash for d in details['cache_handles']]
            if hashlib.sha256('Standard Data: Swarm.SC2Mod'.encode('utf8')).hexdigest() in dependency_hashes:
                self.expansion = 'HotS'
            elif hashlib.sha256('Standard Data: Liberty.SC2Mod'.encode('utf8')).hexdigest() in dependency_hashes:
                self.expansion = 'WoL'
            else:
                self.expansion = ''

            self.windows_timestamp = details['file_time']
            self.unix_timestamp = utils.windows_to_unix(self.windows_timestamp)
            self.end_time = datetime.utcfromtimestamp(self.unix_timestamp)

            # The utc_adjustment is either the adjusted windows timestamp OR
            # the value required to get the adjusted timestamp. We know the upper
            # limit for any adjustment number so use that to distinguish between
            # the two cases.
            if details['utc_adjustment'] < 10**7*60*60*24:
                self.time_zone = details['utc_adjustment']/(10**7*60*60)
            else:
                self.time_zone = (details['utc_adjustment']-details['file_time'])/(10**7*60*60)

            self.game_length = self.length
            self.real_length = utils.Length(seconds=int(self.length.seconds/GAME_SPEED_FACTOR[self.speed]))
            self.start_time = datetime.utcfromtimestamp(self.unix_timestamp-self.real_length.seconds)
            self.date = self.end_time  # backwards compatibility

    def load_map(self):
        self.map = self.factory.load_map(self.map_file, **self.opt)

    def load_players(self):
        #If we don't at least have details and attributes_events we can go no further
        if 'replay.details' not in self.raw_data:
            return
        if 'replay.attributes.events' not in self.raw_data:
            return
        if 'replay.initData' not in self.raw_data:
            return

        self.clients = list()
        self.client = dict()

        # For players, we can use the details file to look up additional
        # information. detail_id marks the current index into this data.
        detail_id = 0
        player_id = 1
        details = self.raw_data['replay.details']
        initData = self.raw_data['replay.initData']

        # Assume that the first X map slots starting at 1 are player slots
        # so that we can assign player ids without the map
        self.entities = list()
        for slot_id, slot_data in enumerate(initData['lobby_state']['slots']):
            user_id = slot_data['user_id']

            if slot_data['control'] == 2:
                if slot_data['observe'] == 0:
                    self.entities.append(Participant(slot_id, slot_data, user_id, initData['user_initial_data'][user_id], player_id, details['players'][detail_id], self.attributes.get(player_id, dict())))
                    detail_id += 1
                    player_id += 1

                else:
                    self.entities.append(Observer(slot_id, slot_data, user_id, initData['user_initial_data'][user_id], player_id))
                    player_id += 1

            elif slot_data['control'] == 3:
                self.entities.append(Computer(slot_id, slot_data, player_id, details['players'][detail_id], self.attributes.get(player_id, dict())))
                detail_id += 1
                player_id += 1

        def get_team(team_id):
            if team_id is not None and team_id not in self.team:
                    team = Team(team_id)
                    self.team[team_id] = team
                    self.teams.append(team)
            return self.team[team_id]

        # Set up all our cross reference data structures
        for entity in self.entities:
            if entity.is_observer is False:
                entity.team = get_team(entity.team_id)
                entity.team.players.append(entity)
                self.players.append(entity)
                self.player[entity.pid] = entity
            else:
                self.observers.append(entity)
                self.observer[entity.uid] = entity

            if entity.is_human:
                self.humans.append(entity)
                self.human[entity.uid] = entity
            else:
                self.computers.append(entity)
                self.computer[entity.pid] = entity

            # Index by pid so that we can match events to players in pre-HotS replays
            self.entity[entity.pid] = entity

        # Pull results up for teams
        for team in self.teams:
            results = set([p.result for p in team.players])
            if len(results) == 1:
                team.result = list(results)[0]
                if team.result == 'Win':
                    self.winner = team
            else:
                self.logger.warn("Conflicting results for Team {0}: {1}".format(team.number, results))
                team.result = 'Unknown'

        self.teams.sort(key=lambda t: t.number)

        # These are all deprecated
        self.clients = self.humans
        self.people = self.entities
        self.client = self.human
        self.person = self.entity

        self.real_type = utils.get_real_type(self.teams)

        # Assign the default region to computer players for consistency
        # We know there will be a default region because there must be
        # at least 1 human player or we wouldn't have a replay.
        default_region = self.humans[0].region
        for entity in self.entities:
            if not entity.region:
                entity.region = default_region

        # Pretty sure this just never worked, forget about it for now
        self.recorder = None

        entity_names = sorted(map(lambda p: p.name, self.entities))
        hash_input = self.gateway+":"+','.join(entity_names)
        self.people_hash = hashlib.sha256(hash_input.encode('utf8')).hexdigest()

        # The presence of observers and/or computer players makes this not actually ladder
        # This became an issue in HotS where Training, vs AI, Unranked, and Ranked
        # were all marked with "amm" => Ladder
        if len(self.observers) > 0 or len(self.humans) != len(self.players):
            self.is_ladder = False

    def load_message_events(self):
        if 'replay.message.events' not in self.raw_data:
            return

        self.messages = self.raw_data['replay.message.events'].messages
        self.pings = self.raw_data['replay.message.events'].pings
        self.packets = self.raw_data['replay.message.events'].packets

        self.message_events = self.messages+self.pings+self.packets
        self.events = sorted(self.events + self.message_events, key=lambda e: e.frame)

    def load_game_events(self):
        # Copy the events over
        # TODO: the events need to be fixed both on the reader and processor side
        if 'replay.game.events' not in self.raw_data:
            return

        self.game_events = self.raw_data['replay.game.events']
        self.events = sorted(self.events+self.game_events, key=lambda e: e.frame)

        # hideous hack for HotS 2.0.0.23925, see https://github.com/GraylinKim/sc2reader/issues/87
        if self.events and self.events[-1].frame > self.frames:
            self.frames = self.events[-1].frame
            self.length = utils.Length(seconds=int(self.frames/self.game_fps))

    def load_tracker_events(self):
        if 'replay.tracker.events' not in self.raw_data:
            return

        self.tracker_events = self.raw_data['replay.tracker.events']
        self.events = sorted(self.tracker_events + self.events, key=lambda e: e.frame)

    def register_reader(self, data_file, reader, filterfunc=lambda r: True):
        """
        Allows you to specify your own reader for use when reading the data
        files packed into the .SC2Replay archives. Datapacks are checked for
        use with the supplied filterfunc in reverse registration order to give
        user registered datapacks preference over factory default datapacks.

        Don't use this unless you know what you are doing.

        :param data_file: The full file name that you would like this reader to
            parse.

        :param reader: The :class:`Reader` object you wish to use to read the
            data file.

        :param filterfunc: A function that accepts a partially loaded
            :class:`Replay` object as an argument and returns true if the
            reader should be used on this replay.
        """
        self.registered_readers[data_file].insert(0, (filterfunc, reader))

    def register_datapack(self, datapack, filterfunc=lambda r: True):
        """
        Allows you to specify your own datapacks for use when loading replays.
        Datapacks are checked for use with the supplied filterfunc in reverse
        registration order to give user registered datapacks preference over
        factory default datapacks.

        This is how you would add mappings for your favorite custom map.

        :param datapack: A :class:`BaseData` object to use for mapping unit
            types and ability codes to their corresponding classes.

        :param filterfunc: A function that accepts a partially loaded
            :class:`Replay` object as an argument and returns true if the
            datapack should be used on this replay.
        """
        self.registered_datapacks.insert(0, (filterfunc, datapack))

    # Override points
    def register_default_readers(self):
        """Registers factory default readers."""
        self.register_reader('replay.details', readers.DetailsReader(), lambda r: True)
        self.register_reader('replay.initData', readers.InitDataReader(), lambda r: True)
        self.register_reader('replay.tracker.events', readers.TrackerEventsReader(), lambda r: True)
        self.register_reader('replay.message.events', readers.MessageEventsReader(), lambda r: True)
        self.register_reader('replay.attributes.events', readers.AttributesEventsReader(), lambda r: True)

        self.register_reader('replay.game.events', readers.GameEventsReader_15405(), lambda r: 15405 <= r.base_build < 16561)
        self.register_reader('replay.game.events', readers.GameEventsReader_16561(), lambda r: 16561 <= r.base_build < 17326)
        self.register_reader('replay.game.events', readers.GameEventsReader_17326(), lambda r: 17326 <= r.base_build < 18574)
        self.register_reader('replay.game.events', readers.GameEventsReader_18574(), lambda r: 18574 <= r.base_build < 19595)
        self.register_reader('replay.game.events', readers.GameEventsReader_19595(), lambda r: 19595 <= r.base_build < 22612)
        self.register_reader('replay.game.events', readers.GameEventsReader_22612(), lambda r: 22612 <= r.base_build < 23260)
        self.register_reader('replay.game.events', readers.GameEventsReader_23260(), lambda r: 23260 <= r.base_build < 24247)
        self.register_reader('replay.game.events', readers.GameEventsReader_24247(), lambda r: 24247 <= r.base_build < 26490)
        self.register_reader('replay.game.events', readers.GameEventsReader_26490(), lambda r: 26490 <= r.base_build)
        self.register_reader('replay.game.events', readers.GameEventsReader_HotSBeta(), lambda r: r.versions[1] == 2 and r.build < 24247)


    def register_default_datapacks(self):
        """Registers factory default datapacks."""
        self.register_datapack(datapacks['WoL']['16117'], lambda r: r.expansion == 'WoL' and 16117 <= r.build < 17326)
        self.register_datapack(datapacks['WoL']['17326'], lambda r: r.expansion == 'WoL' and 17326 <= r.build < 18092)
        self.register_datapack(datapacks['WoL']['18092'], lambda r: r.expansion == 'WoL' and 18092 <= r.build < 19458)
        self.register_datapack(datapacks['WoL']['19458'], lambda r: r.expansion == 'WoL' and 19458 <= r.build < 22612)
        self.register_datapack(datapacks['WoL']['22612'], lambda r: r.expansion == 'WoL' and 22612 <= r.build < 24944)
        self.register_datapack(datapacks['WoL']['24944'], lambda r: r.expansion == 'WoL' and 24944 <= r.build)
        self.register_datapack(datapacks['HotS']['base'], lambda r: r.expansion == 'HotS' and r.build < 23925)
        self.register_datapack(datapacks['HotS']['23925'], lambda r: r.expansion == 'HotS' and 23925 <= r.build < 24247)
        self.register_datapack(datapacks['HotS']['24247'], lambda r: r.expansion == 'HotS' and 24247 <= r.build <= 24764)
        self.register_datapack(datapacks['HotS']['24764'], lambda r: r.expansion == 'HotS' and 24764 <= r.build)

    # Internal Methods
    def _get_reader(self, data_file):
        for callback, reader in self.registered_readers[data_file]:
            if callback(self):
                return reader
        else:
            raise ValueError("Valid {0} reader could not found for build {1}".format(data_file, self.build))

    def _get_datapack(self):
        for callback, datapack in self.registered_datapacks:
            if callback(self):
                return datapack
        else:
            return None

    def _read_data(self, data_file, reader):
        data = utils.extract_data_file(data_file, self.archive)
        if data:
            self.raw_data[data_file] = reader(data, self)
        elif self.opt.debug and data_file not in ['replay.message.events', 'replay.tracker.events']:
            raise ValueError("{0} not found in archive".format(data_file))


class Map(Resource):
    url_template = 'http://{0}.depot.battle.net:1119/{1}.s2ma'

    #: The localized (only enUS supported right now) map name
    name = str()

    #: The map's author
    author = str()

    #: The map description as written by author
    description = str()

    def __init__(self, map_file, filename=None, gateway=None, map_hash=None, **options):
        super(Map, self).__init__(map_file, filename, **options)

        #: The unique hash used to identify this map on bnet's depots.
        self.hash = map_hash

        #: The gateway this map was posted to. Maps must be posted individually to each gateway.
        self.gateway = gateway

        #: A URL reference to the location of this map on bnet's depots.
        self.url = Map.get_url(gateway, map_hash)

        #: The opened MPQArchive for this map
        self.archive = mpyq.MPQArchive(map_file)

        #: A byte string representing the minimap in tga format.
        self.minimap = self.archive.read_file('Minimap.tga')

        # This will only populate the fields for maps with enUS localizations.
        # Clearly this isn't a great solution but we can't be throwing exceptions
        # just because US English wasn't a concern of the map author.
        # TODO: Make this work regardless of the localizations available.
        game_strings = self.archive.read_file('enUS.SC2Data\LocalizedData\GameStrings.txt').decode('utf8')
        if game_strings:
            for line in game_strings.split('\r\n'):
                if len(line) == 0:
                    continue

                key, value = line.split('=', 1)
                if key == 'DocInfo/Name':
                    self.name = value
                elif key == 'DocInfo/Author':
                    self.author = value
                elif key == 'DocInfo/DescLong':
                    self.description = value
                elif key == 'DocInfo/Website':
                    self.website = value

        #: A reference to the map's :class:`~sc2reader.objects.MapInfo` object
        self.map_info = MapInfo(self.archive.read_file('MapInfo'))

        doc_info = ElementTree.fromstring(self.archive.read_file('DocumentInfo').decode('utf8'))

        icon_path_node = doc_info.find('Icon/Value')
        #: (Optional) The path to the icon for the map, relative to the archive root
        self.icon_path = icon_path_node.text if icon_path_node is not None else None

        #: (Optional) The icon image for the map in tga format
        self.icon = self.archive.read_file(self.icon_path) if self.icon_path is not None else None

        #: A list of module names this map depends on
        self.dependencies = list()
        for dependency_node in doc_info.findall('Dependencies/Value'):
            self.dependencies.append(dependency_node.text)

    @classmethod
    def get_url(cls, gateway, map_hash):
        """Builds a download URL for the map from its components."""
        if gateway and map_hash:
            # it seems like sea maps are stored on us depots.
            gateway = 'us' if gateway == 'sea' else gateway
            return cls.url_template.format(gateway, map_hash)
        else:
            return None


class Localization(Resource, dict):

    def __init__(self, s2ml_file, **options):
        Resource.__init__(self, s2ml_file, **options)
        xml = ElementTree.parse(s2ml_file)
        for entry in xml.findall('e'):
            self[int(entry.attrib['id'])] = entry.text


class GameSummary(Resource):

    url_template = 'http://{0}.depot.battle.net:1119/{1}.s2gs'

    #: Game speed
    game_speed = str()

    #: Game length (real-time)
    real_length = int()

    #: Game length (in-game)
    game_length = int()

    #: A dictionary of Lobby properties
    lobby_properties = dict()

    #: A dictionary of Lobby player properties
    lobby_player_properties = dict()

    #: Winners, a list of the pids of the winning players
    winners = list()

    #: Build orders, a dict of build orders indexed by player id
    build_orders = dict()

    #: Map image urls
    image_urls = list()

    #: Map localization urls
    localization_urls = dict()

    def __init__(self, summary_file, filename=None, lang='enUS', **options):
        super(GameSummary, self).__init__(summary_file, filename, lang=lang, **options)

        #: A dict of team# -> teams
        self.team = dict()

        #: A list of teams
        self.teams = list()

        #: Players, a dict of :class`PlayerSummary` from the game
        self.players = list()

        self.observers = list()

        #: Game start and end times
        self.start_time = None
        self.end_time = None

        self.winners = list()
        self.player = dict()
        self.settings = dict()
        self.player_stats = dict()
        self.player_settings = defaultdict(dict)
        self.build_orders = defaultdict(list)
        self.image_urls = list()
        self.localization_urls = dict()
        self.lobby_properties = dict()
        self.lobby_player_properties = dict()
        self.game_type = str()
        self.real_type = str()

        # The first 16 bytes appear to be some sort of compression header
        buffer = BitPackedDecoder(zlib.decompress(summary_file.read()[16:]))

        # TODO: Is there a fixed number of entries?
        # TODO: Maybe the # of parts is recorded somewhere?
        self.parts = list()
        while not buffer.done():
            self.parts.append(buffer.read_struct())

        self.end_time = datetime.utcfromtimestamp(self.parts[0][8])
        self.game_speed = LOBBY_PROPERTIES[0xBB8][1][self.parts[0][0][1].decode('utf8')]
        self.game_length = utils.Length(seconds=self.parts[0][7])
        self.real_length = utils.Length(seconds=int(self.parts[0][7]/GAME_SPEED_FACTOR[self.game_speed]))
        self.start_time = datetime.utcfromtimestamp(self.parts[0][8] - self.real_length.seconds)

        self.load_translations()
        self.load_map_info()
        self.load_settings()
        self.load_player_stats()
        self.load_players()

        dependencies = [sheet[1] for sheet in self.lang_sheets['enUS']]
        if 'Swarm (Mod)' in dependencies:
            self.expansion = 'HotS'
        elif 'Liberty (Mod)' in dependencies:
            self.expansion = 'WoL'
        else:
            self.expansion = ''

        self.game_type = self.settings['Teams'].replace(" ", "")
        self.real_type = utils.get_real_type(self.teams)

        # The s2gs file also keeps reference to a series of s2mv files
        # Some of these appear to be encoded bytes and others appear to be
        # the preview images that authors may bundle with their maps.
        self.s2mv_urls = [str(utils.DepotFile(file_hash)) for file_hash in self.parts[0][6][7]]

    def load_translations(self):
        # This section of the file seems to map numerical ids to their
        # corresponding entries in the localization files (see below).
        # Each mapping has 3 parts:
        #   1: The id to be mapped
        #   2: The localization sheet and entry index to map to
        #   3: A list of ids for the summary tabs the value shows up in
        #
        # In some cases it seems that these ids don't map to an entry so
        # there must be some additional purpose to this section as well.
        #
        self.id_map = dict()
        for mapping in self.parts[1][0]:
            if isinstance(mapping[2][0], dict):
                self.id_map[mapping[1][1]] = (mapping[2][0][1], mapping[2][0][2])

        # The id mappings for lobby and player properties are stored
        # separately in the parts[0][5] entry.
        #
        # The values for each property are also mapped but the values
        # don't have their own unique ids so we use a compound key
        self.lobby_properties = dict()
        for item in self.parts[0][5]:
            uid = item[0][1]
            sheet = item[2][0][1]
            entry = item[2][0][2]
            self.id_map[uid] = (sheet, entry)

            for value in item[1]:
                sheet = value[1][0][1]
                entry = value[1][0][2]
                self.id_map[(uid, value[0])] = (sheet, entry)

        # Each localization is a pairing of a language id, e.g. enUS
        # and a list of byte strings that can be decoded into urls for
        # resources hosted on the battle.net depot servers.
        #
        # Sometimes these byte strings are all NULLed out and need to be ignored.
        for localization in self.parts[0][6][8]:
            language = localization[0].decode('utf8')

            files = list()
            for file_hash in localization[1]:
                if file_hash[:4].decode('utf8') != '\x00\x00\x00\x00':
                    files.append(utils.DepotFile(file_hash))
            self.localization_urls[language] = files

        # Grab the gateway from the one of the files
        self.gateway = list(self.localization_urls.values())[0][0].server.lower()

        # Each of the localization urls points to an XML file with a set of
        # localization strings and their unique ids. After reading these mappings
        # into a lang_sheets variable we can use these sheets to make a direct
        # map from internal id to localize string.
        #
        # For now we'll only do this for english localizations.
        self.lang_sheets = dict()
        self.translations = dict()
        for lang, files in self.localization_urls.items():
            if lang != self.opt.lang:
                continue

            sheets = list()
            for depot_file in files:
                sheets.append(self.factory.load_localization(depot_file, **self.opt))

            translation = dict()
            for uid, (sheet, item) in self.id_map.items():
                if sheet < len(sheets) and item in sheets[sheet]:
                    translation[uid] = sheets[sheet][item]
                elif self.opt.debug:
                    msg = "No {0} translation for sheet {1}, item {2}"
                    raise SC2ReaderLocalizationError(msg.format(self.opt.lang, sheet, item))
                else:
                    translation[uid] = "Unknown"

            self.lang_sheets[lang] = sheets
            self.translations[lang] = translation

    def load_map_info(self):
        map_strings = self.lang_sheets[self.opt.lang][-1]
        self.map_name = map_strings[1]
        self.map_description = map_strings[2]
        self.map_tileset = map_strings[3]

    def load_settings(self):
        Property = namedtuple('Property', ['id', 'values', 'requirements', 'defaults', 'is_lobby'])

        properties = dict()
        for p in self.parts[0][5]:
            properties[p[0][1]] = Property(p[0][1], p[1], p[3], p[8], isinstance(p[8], dict))

        settings = dict()
        for setting in self.parts[0][6][6]:
            prop = properties[setting[0][1]]
            if prop.is_lobby:
                settings[setting[0][1]] = setting[1][0]
            else:
                settings[setting[0][1]] = [p[0] for p in setting[1]]

        activated = dict()

        def use_property(prop, player=None):
            # Check the cache before recomputing
            if (prop.id, player) in activated:
                return activated[(prop.id, player)]

            # A property can only be used if it's requirements
            # are both active and have one if the required settings.
            # These settings can be player specific.
            use = False
            for req in prop.requirements:
                requirement = properties[req[0][1][1]]
                if not use_property(requirement, player):
                    break

                setting = settings[req[0][1][1]]

                # Lobby properties can require on player properties.
                # How does this work? I assume that one player satisfying the
                # property requirments is sufficient
                if requirement.is_lobby:
                    values = [setting]
                else:
                    values = [setting[player]] if player is not None else setting

                # Because of the above complication we resort to a set intersection of
                # the applicable values and the set of required values.
                if not set(requirement.values[val][0] for val in values) & set(req[1]):
                    break

            else:
                # Executes if we don't break out of the loop!
                use = True

            # Record the result for future reference and return
            activated[(prop.id, player)] = use
            return use

        translation = self.translations[self.opt.lang]
        for uid, prop in properties.items():
            name = translation.get(uid, "Unknown")
            if prop.is_lobby:
                if use_property(prop):
                    value = prop.values[settings[uid]][0]
                    self.settings[name] = translation[(uid, value)]
            else:
                for index, player_setting in enumerate(settings[uid]):
                    if use_property(prop, index):
                        value = prop.values[player_setting][0]
                        self.player_settings[index][name] = translation[(uid, value)]

    def load_player_stats(self):
        translation = self.translations[self.opt.lang]

        stat_items = sum([p[0] for p in self.parts[3:]], [])

        for item in stat_items:
            # Each stat item is laid out as follows
            #
            #   {
            #     0: {0:999, 1:translation_id},
            #     1: [ [{p1values},...], [{p2values},...], ...]
            #   }
            stat_id = item[0][1]
            if stat_id in translation:
                stat_name = translation[stat_id]
                # Assume anything under 1 million is a normal score screen item
                # Build order ids are generally 16 million+
                if stat_id < 1000000:
                    for pid, value in enumerate(item[1]):
                        if not value:
                            continue

                        if stat_name in ('Army Value', 'Resource Collection Rate', 'Upgrade Spending', 'Workers Active'):
                            # Each point entry for the graph is laid out as follows
                            #
                            #   {0:Value, 1:0, 2:Time}
                            #
                            # The 2nd part of the tuple appears to always be zero and
                            # the time is in seconds of game time.
                            xy = [(point[2], point[0]) for point in value]
                            value = Graph([], [], xy_list=xy)
                        else:
                            value = value[0][0]

                        self.player_stats.setdefault(pid, dict())[stat_name] = value
                else:
                    # Each build item represents one ability and contains
                    # a list of all the uses of that ability by each player
                    # up to the first 64 successful actions in the game.
                    for pindex, commands in enumerate(item[1]):
                        for command in commands:
                            self.build_orders[pindex].append(BuildEntry(
                                supply=command[0],
                                total_supply=command[1] & 0xff,
                                time=int((command[2] >> 8) / 16),
                                order=stat_name,
                                build_index=command[1] >> 16
                            ))
            elif stat_id != 83886080:  # We know this one is always bad.
                self.logger.warn("Untranslatable key = {0}".format(stat_id))

        # Once we've compiled all the build commands we need to make
        # sure they are properly sorted for presentation.
        for build_order in self.build_orders.values():
            build_order.sort(key=lambda x: x.build_index)

    def load_players(self):
        for index, struct in enumerate(self.parts[0][3]):
            if not struct[0] or not struct[0][1]:
                continue  # Slot is closed

            player = PlayerSummary(struct[0][0])
            stats = self.player_stats.get(index, dict())
            settings = self.player_settings[index]
            player.is_ai = not isinstance(struct[0][1], dict)
            if not player.is_ai:
                player.gateway = self.gateway
                player.subregion = struct[0][1][0][2]
                player.region = REGIONS[player.gateway].get(player.subregion, 'Unknown')
                player.bnetid = struct[0][1][0][3]
                player.unknown1 = struct[0][1][0]
                player.unknown2 = struct[0][1][1]

            # Either a referee or a spectator, nothing else to do
            if settings.get('Participant Role', '') != 'Participant':
                self.observers.append(player)
                continue

            player.play_race = LOBBY_PROPERTIES[0xBB9][1].get(struct[2], None)

            player.is_winner = isinstance(struct[1], dict) and struct[1][0] == 0
            if player.is_winner:
                self.winners.append(player.pid)

            team_id = int(settings['Team'].split(' ')[1])
            if team_id not in self.team:
                self.team[team_id] = Team(team_id)
                self.teams.append(self.team[team_id])

            player.team = self.team[team_id]
            self.team[team_id].players.append(player)

            # We can just copy these settings right over
            player.color = utils.Color(name=settings.get('Color', None))
            player.pick_race = settings.get('Race', None)
            player.handicap = settings.get('Handicap', None)

            # Overview Tab
            player.resource_score = stats.get('Resources', None)
            player.structure_score = stats.get('Structures', None)
            player.unit_score = stats.get('Units', None)
            player.overview_score = stats.get('Overview', None)

            # Units Tab
            player.units_killed = stats.get('Killed Unit Count', None)
            player.structures_built = stats.get('Structures Built', None)
            player.units_trained = stats.get('Units Trained', None)
            player.structures_razed = stats.get('Structures Razed Count', None)

            # Graphs Tab
            # Keep income_graph for backwards compatibility
            player.army_graph = stats.get('Army Value')
            player.resource_collection_graph = stats.get('Resource Collection Rate', None)
            player.income_graph = player.resource_collection_graph

            # HotS Stats
            player.upgrade_spending_graph = stats.get('Upgrade Spending', None)
            player.workers_active_graph = stats.get('Workers Active', None)
            player.enemies_destroyed = stats.get('Enemies Destroyed:', None)
            player.time_supply_capped = stats.get('Time Supply Capped', None)
            player.idle_production_time = stats.get('Idle Production Time', None)
            player.resources_spent = stats.get('Resources Spent:', None)
            player.apm = stats.get('APM', None)

            # Economic Breakdown Tab
            if isinstance(player.income_graph, Graph):
                values = player.income_graph.values
                player.resource_collection_rate = int(sum(values)/len(values))
            else:
                # In old s2gs files the field with this name was actually a number not a graph
                player.resource_collection_rate = player.income_graph
                player.resource_collection_graph = None
                player.income_graph = None

            player.avg_unspent_resources = stats.get('Average Unspent Resources', None)
            player.workers_created = stats.get('Workers Created', None)

            # Build Orders Tab
            player.build_order = self.build_orders.get(index, None)

            self.players.append(player)
            self.player[player.pid] = player

    def __str__(self):
        return "{0} - {1} {2}".format(self.start_time, self.game_length, 'v'.join(''.join(p.play_race[0] for p in team.players) for team in self.teams))


class MapHeader(Resource):
    """**Experimental**"""

    base_url_template = 'http://{0}.depot.battle.net:1119/{1}.{2}'
    url_template = 'http://{0}.depot.battle.net:1119/{1}.s2mh'
    image_url_template = 'http://{0}.depot.battle.net:1119/{1}.s2mv'

    #: The name of the map
    name = str()

    #: Hash of map file
    map_hash = str()

    #: Link to the map file
    map_url = str()

    #: Hash of the map image
    image_hash = str()

    #: Link to the image of the map (.s2mv)
    image_url = str()

    #: Localization dictionary, {language, url}
    localization_urls = dict()

    #: Blizzard map
    blizzard = False

    def __init__(self, header_file, filename=None, **options):
        super(MapHeader, self).__init__(header_file, filename, **options)
        self.data = BitPackedDecoder(header_file).read_struct()

        # Name
        self.name = self.data[0][1]

        # Blizzard
        self.blizzard = (self.data[0][11] == 'BLIZ')

        # Parse image hash
        parsed_hash = utils.parse_hash(self.data[0][1])
        self.image_hash = parsed_hash['hash']
        self.image_url = self.image_url_template.format(parsed_hash['server'], parsed_hash['hash'])

        # Parse map hash
        parsed_hash = utils.parse_hash(self.data[0][2])
        self.map_hash = parsed_hash['hash']
        self.map_url = self.base_url_template.format(parsed_hash['server'], parsed_hash['hash'], parsed_hash['type'])

        # Parse localization hashes
        l18n_struct = self.data[0][4][8]
        for l in l18n_struct:
            parsed_hash = utils.parse_hash(l[1][0])
            self.localization_urls[l[0]] = self.base_url_template.format(parsed_hash['server'], parsed_hash['hash'], parsed_hash['type'])
