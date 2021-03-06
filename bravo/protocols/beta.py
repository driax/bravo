from collections import namedtuple, defaultdict
from itertools import product, chain
from time import time
from urlparse import urlunparse
from math import pi

from twisted.internet import reactor
from twisted.internet.defer import (DeferredList, inlineCallbacks,
    maybeDeferred, succeed)
from twisted.internet.protocol import Protocol
from twisted.internet.task import cooperate, deferLater, LoopingCall
from twisted.internet.task import TaskDone, TaskFailed
from twisted.python import log
from twisted.web.client import getPage

from bravo.blocks import blocks, items
from bravo.config import configuration
from bravo.entity import Sign
from bravo.factories.infini import InfiniClientFactory
from bravo.ibravo import (IChatCommand, IPreBuildHook, IPostBuildHook,
    IDigHook, ISignHook, IUseHook)
from bravo.inventory import Workbench, sync_inventories
from bravo.location import Location
from bravo.motd import get_motd
from bravo.packets.beta import parse_packets, make_packet, make_error_packet
from bravo.plugin import retrieve_plugins, retrieve_sorted_plugins, retrieve_named_plugins
from bravo.policy.dig import dig_policies
from bravo.utilities.coords import split_coords

(STATE_UNAUTHENTICATED, STATE_CHALLENGED, STATE_AUTHENTICATED) = range(3)

SUPPORTED_PROTOCOL = 11

circle = [(i, j)
    for i, j in product(xrange(-10, 10), xrange(-10, 10))
    if i**2 + j**2 <= 100
]
"""
A list of points in a filled circle of radius 10.
"""

BuildData = namedtuple("BuildData", "block, metadata, x, y, z, face")

class BuildError(Exception):
    """
    Something went wrong with the build.
    """

class BetaServerProtocol(Protocol):
    """
    The Minecraft Alpha/Beta server protocol.

    This class is mostly designed to be a skeleton for featureful clients. It
    tries hard to not step on the toes of potential subclasses.
    """

    excess = ""
    packet = None

    state = STATE_UNAUTHENTICATED

    buf = ""
    parser = None
    handler = None

    player = None
    username = None

    def __init__(self):
        self.chunks = dict()
        self.windows = dict()
        self.wid = 1

        self.location = Location()

        self.handlers = {
            0: self.ping,
            1: self.login,
            2: self.handshake,
            3: self.chat,
            7: self.use,
            10: self.grounded,
            11: self.position,
            12: self.orientation,
            13: self.location_packet,
            14: self.digging,
            15: self.build,
            16: self.equip,
            18: self.animate,
            19: self.action,
            21: self.pickup,
            101: self.wclose,
            102: self.waction,
            104: self.inventory,
            106: self.wacknowledge,
            130: self.sign,
            255: self.quit,
        }

        self._ping_loop = LoopingCall(self.update_ping)

    # Low-level packet handlers
    # Try not to hook these if possible, since they offer no convenient
    # abstractions or protections.

    def ping(self, container):
        """
        Hook for ping packets.
        """

    def login(self, container):
        """
        Hook for login packets.

        Override this to customize how logins are handled. By default, this
        method will only confirm that the negotiated wire protocol is the
        correct version, and then it will run the ``authenticated()``
        callback.
        """

        if container.protocol < SUPPORTED_PROTOCOL:
            # Kick old clients.
            self.error("This server doesn't support your ancient client.")
        elif container.protocol > SUPPORTED_PROTOCOL:
            # Kick new clients.
            self.error("This server doesn't support your newfangled client.")
        else:
            reactor.callLater(0, self.authenticated)

    def handshake(self, container):
        """
        Hook for handshake packets.
        """

    def chat(self, container):
        """
        Hook for chat packets.
        """

    def use(self, container):
        """
        Hook for use packets.
        """

    def grounded(self, container):
        """
        Hook for grounded packets.
        """

        self.location.grounded = bool(container.grounded)

    def position(self, container):
        """
        Hook for position packets.
        """

        old_position = self.location.x, self.location.y, self.location.z

        # Location represents the block the player is within
        self.location.x = int(container.position.x) if container.position.x > 0 else int(container.position.x) - 1
        self.location.y = int(container.position.y)
        self.location.z = int(container.position.z) if container.position.z > 0 else int(container.position.z) - 1
        # Stance is the current jumping position, plus a small offset of
        # around 0.1. In the Alpha server, it must between 0.1 and 1.65,
        # or the anti-grounded code kicks the client.
        self.location.stance = container.position.stance

        position = self.location.x, self.location.y, self.location.z

        self.grounded(container.grounded)

        if old_position != position:
            self.position_changed()

    def orientation(self, container):
        """
        Hook for orientation packets.
        """

        old_orientation = self.location.yaw, self.location.pitch

        self.location.yaw = container.orientation.rotation
        self.location.pitch = container.orientation.pitch

        orientation = self.location.yaw, self.location.pitch

        self.grounded(container.grounded)

        if old_orientation != orientation:
            self.orientation_changed()

    def location_packet(self, container):
        """
        Hook for location packets.
        """

        self.position(container)
        self.orientation(container)

    def digging(self, container):
        """
        Hook for digging packets.
        """

    def build(self, container):
        """
        Hook for build packets.
        """

    def equip(self, container):
        """
        Hook for equip packets.
        """

    def pickup(self, container):
        """
        Hook for pickup packets.
        """

    def animate(self, container):
        """
        Hook for animate packets.
        """

    def action(self, container):
        """
        Hook for action packets.
        """

    def wclose(self, container):
        """
        Hook for wclose packets.
        """

    def waction(self, container):
        """
        Hook for waction packets.
        """

    def inventory(self, container):
        """
        Hook for inventory packets.
        """

    def wacknowledge(self, container):
        """
        Hook for wacknowledge packets.
        """

    def sign(self, container):
        """
        Hook for sign packets.
        """

    def quit(self, container):
        """
        Hook for quit packets.

        By default, merely logs the quit message and drops the connection.

        Even if the connection is not dropped, it will be lost anyway since
        the client will close the connection. It's better to explicitly let it
        go here than to have zombie protocols.
        """

        log.msg("Client is quitting: %s" % container.message)
        self.transport.loseConnection()

    # Twisted-level data handlers and methods
    # Please don't override these needlessly, as they are pretty solid and
    # shouldn't need to be touched.

    def dataReceived(self, data):
        self.buf += data

        packets, self.buf = parse_packets(self.buf)

        for header, payload in packets:
            if header in self.handlers:
                self.handlers[header](payload)
            else:
                log.err("Didn't handle parseable packet %d!" % header)
                log.err(payload)

    def connectionLost(self, reason):
        if self._ping_loop.running:
            self._ping_loop.stop()

    # State-change callbacks
    # Feel free to override these, but call them at some point.

    def challenged(self):
        """
        Called when the client has started authentication with the server.
        """

        self.state = STATE_CHALLENGED

    def authenticated(self):
        """
        Called when the client has successfully authenticated with the server.
        """

        self.state = STATE_AUTHENTICATED

        self._ping_loop.start(5)

    # Event callbacks
    # These are meant to be overriden.

    def orientation_changed(self):
        """
        Called when the client moves.

        This callback is only for orientation, not position.
        """

        pass

    def position_changed(self):
        """
        Called when the client moves.

        This callback is only for position, not orientation.
        """

        pass

    # Convenience methods

    def update_ping(self):
        """
        Send a keepalive to the client.
        """

        packet = make_packet("ping")
        self.transport.write(packet)

    def error(self, message):
        """
        Error out.

        This method sends ``message`` to the client as a descriptive error
        message, then closes the connection.
        """

        self.transport.write(make_error_packet(message))
        self.transport.loseConnection()


class BannedProtocol(BetaServerProtocol):
    """
    A very simple Beta protocol that helps enforce IP bans.

    This protocol disconnects people as soon as they connect, with a helpful
    message.
    """

    def connectionMade(self):
        self.error("Sorry, but your IP address is banned.")


class BetaProxyProtocol(BetaServerProtocol):
    """
    A ``BetaServerProtocol`` that proxies for an InfiniCraft client.
    """

    gateway = "server.wiki.vg"

    def handshake(self, container):
        packet = make_packet("handshake", username="-")
        self.transport.write(packet)

    def login(self, container):
        self.username = container.username

        packet = make_packet("login", protocol=0, username="", seed=0,
            dimension=0)
        self.transport.write(packet)

        url = urlunparse(("http", self.gateway, "/node/0/0/", None, None,
            None))
        d = getPage(url)
        d.addCallback(self.start_proxy)

    def start_proxy(self, response):
        log.msg("Response: %s" % response)
        log.msg("Starting proxy...")
        address, port = response.split(":")
        self.add_node(address, int(port))

    def add_node(self, address, port):
        """
        Add a new node to this client.
        """

        from twisted.internet.endpoints import TCP4ClientEndpoint

        log.msg("Adding node %s:%d" % (address, port))

        endpoint = TCP4ClientEndpoint(reactor, address, port, 5)

        self.node_factory = InfiniClientFactory()
        d = endpoint.connect(self.node_factory)
        d.addCallback(self.node_connected)
        d.addErrback(self.node_connect_error)

    def node_connected(self, protocol):
        log.msg("Connected new node!")

    def node_connect_error(self, reason):
        log.err("Couldn't connect node!")
        log.err(reason)


class BravoProtocol(BetaServerProtocol):
    """
    A ``BetaServerProtocol`` suitable for serving MC worlds to clients.

    This protocol really does need to be hooked up with a ``BravoFactory`` or
    something very much like it.
    """

    chunk_tasks = None

    time_loop = None

    eid = 0

    last_dig = None

    def __init__(self, name):
        BetaServerProtocol.__init__(self)

        self.config_name = "world %s" % name

        log.msg("Registering client hooks...")
        names = configuration.getlistdefault(self.config_name, "pre_build_hooks",
            [])
        self.pre_build_hooks = retrieve_sorted_plugins(IPreBuildHook, names)
        names = configuration.getlistdefault(self.config_name, "post_build_hooks",
            [])
        self.post_build_hooks = retrieve_sorted_plugins(IPostBuildHook, names)
        names = configuration.getlistdefault(self.config_name, "dig_hooks",
            [])
        self.dig_hooks = retrieve_sorted_plugins(IDigHook, names)
        names = configuration.getlistdefault(self.config_name, "sign_hooks",
            [])
        self.sign_hooks = retrieve_sorted_plugins(ISignHook, names)

        names = configuration.getlistdefault(self.config_name, "use_hooks",
            [])
        self.use_hooks = defaultdict(list)
        for plugin in retrieve_named_plugins(IUseHook, names):
            for target in plugin.targets:
                self.use_hooks[target].append(plugin)

        log.msg("Registering policies...")
        self.dig_policy = dig_policies["notchy"]

        # Retrieve the MOTD. Only needs to be done once.
        self.motd = configuration.getdefault(self.config_name, "motd", None)

    @inlineCallbacks
    def authenticated(self):
        BetaServerProtocol.authenticated(self)

        # Init player, and copy data into it.
        self.player = yield self.factory.world.load_player(self.username)
        self.player.eid = self.eid
        self.location = self.player.location

        # Announce our presence.
        packet = make_packet("chat",
            message="%s is joining the game..." % self.username)
        self.factory.broadcast(packet)

        # Craft our avatar and send it to already-connected other players.
        packet = self.player.save_to_packet()
        packet += make_packet("create", eid=self.player.eid)
        self.factory.broadcast_for_others(packet, self)

        # And of course spawn all of those players' avatars in our client as
        # well. Note that, at this point, we are not listed in the factory's
        # list of protocols, so we won't accidentally send one of these to
        # ourselves.
        for protocol in self.factory.protocols.itervalues():
            packet = protocol.player.save_to_packet()
            packet += protocol.player.save_equipment_to_packet()
            packet += make_packet("create", eid=protocol.player.eid)
            self.transport.write(packet)

        self.factory.protocols[self.username] = self

        # Send spawn and inventory.
        spawn = self.factory.world.spawn
        packet = make_packet("spawn", x=spawn[0], y=spawn[1], z=spawn[2])
        packet += self.player.inventory.save_to_packet()
        self.transport.write(packet)

        self.send_initial_chunk_and_location()

        self.time_loop = LoopingCall(self.update_time)
        self.time_loop.start(10)

    def orientation_changed(self):
        # Bang your head!
        packet = make_packet("entity-orientation",
            eid=self.player.eid,
            yaw=int(self.location.theta * 255 / (2 * pi)) % 256,
            pitch=int(self.location.phi * 255 / (2 * pi)) % 256,
        )
        self.factory.broadcast_for_others(packet, self)

    def position_changed(self):
        x, chaff, z, chaff = split_coords(self.location.x, self.location.z)

        # Inform everybody of our new location.
        packet = make_packet("teleport",
            eid=self.player.eid,
            x=self.location.x * 32,
            y=self.location.y * 32,
            z=self.location.z * 32,
            yaw=int(self.location.theta * 255 / (2 * pi)) % 256,
            pitch=int(self.location.phi * 255 / (2 * pi)) % 256,
        )
        self.factory.broadcast_for_others(packet, self)

        self.update_chunks()

        for entity in self.entities_near(2):
            if entity.name != "Item":
                continue

            if self.player.inventory.add(entity.item, entity.quantity):
                packet = make_packet("collect", eid=entity.eid,
                    destination=self.player.eid)
                self.factory.broadcast(packet)

                packet = make_packet("destroy", eid=entity.eid)
                self.factory.broadcast(packet)

                packet = self.player.inventory.save_to_packet()
                self.transport.write(packet)

                self.factory.destroy_entity(entity)

    def entities_near(self, radius):
        """
        Obtain the entities within a radius of this player.

        Radius is measured in blocks.
        """

        chunk_radius = int(radius // 16 + 1)
        chunkx, chaff, chunkz, chaff = split_coords(self.location.x,
            self.location.z)

        minx = chunkx - chunk_radius
        maxx = chunkx + chunk_radius + 1
        minz = chunkz - chunk_radius
        maxz = chunkz + chunk_radius + 1

        for x, z in product(xrange(minx, maxx), xrange(minz, maxz)):
            chunk = self.chunks[x, z]
            yieldables = [entity for entity in chunk.entities
                if self.location.distance(entity.location) <= radius]
            for i in yieldables:
                yield i

    def login(self, container):
        """
        Handle a login packet.

        This method wraps a login hook which is permitted to do just about
        anything, as long as it's asynchronous. The hook returns a
        ``Deferred``, which is chained to authenticate the user or disconnect
        them depending on the results of the authentication.
        """

        if container.protocol < SUPPORTED_PROTOCOL:
            # Kick old clients.
            self.error("This server doesn't support your ancient client.")
            return
        elif container.protocol > SUPPORTED_PROTOCOL:
            # Kick new clients.
            self.error("This server doesn't support your newfangled client.")
            return

        log.msg("Authenticating client, protocol version %d" %
            container.protocol)

        d = self.factory.login_hook(self, container)
        d.addErrback(lambda *args, **kwargs: self.transport.loseConnection())
        d.addCallback(lambda *args, **kwargs: self.authenticated())

    def handshake(self, container):
        if not self.factory.handshake_hook(self, container):
            self.transport.loseConnection()

    def chat(self, container):
        if container.message.startswith("/"):

            commands = retrieve_plugins(IChatCommand)
            # Register aliases.
            for plugin in commands.values():
                for alias in plugin.aliases:
                    commands[alias] = plugin

            params = container.message[1:].split(" ")
            command = params.pop(0).lower()

            if command and command in commands:
                def cb(iterable):
                    for line in iterable:
                        self.transport.write(
                            make_packet("chat", message=line)
                        )
                def eb(error):
                    self.transport.write(
                        make_packet("chat", message="Error: %s" %
                                    error.getErrorMessage())
                    )
                d = maybeDeferred(commands[command].chat_command,
                                  self.factory, self.username, params)
                d.addCallback(cb)
                d.addErrback(eb)
            else:
                self.transport.write(
                    make_packet("chat",
                        message="Unknown command: %s" % command)
                )
        else:
            # Send the message up to the factory to be chatified.
            message = "<%s> %s" % (self.username, container.message)
            self.factory.chat(message)

    def use(self, container):
        """
        For each entity in proximity (4 blocks), check if it is the target
        of this packet and call all hooks that stated interested in this
        type.
        """
        nearby_players = self.factory.players_near(self.player, 4)
        for entity in chain(self.entities_near(4), nearby_players):
            if entity.eid == container.target:
                for hook in self.use_hooks[entity.name]:
                    hook.use_hook(self.factory, self.player, entity,
                        container.button == 0)
                break

    def digging(self, container):
        if container.x == -1 and container.z == -1 and container.y == 255:
            # Lala-land dig packet. Discard it for now.
            return

        # Player drops currently holding item/block.
        if (container.state == "dropped" and container.face == "-y" and
            container.x == 0 and container.y == 0 and container.z == 0):
            i = self.player.inventory
            holding = i.holdables[self.player.equipped]
            if holding:
                primary, secondary, count = holding
                if i.consume((primary, secondary), self.player.equipped):
                    dest = self.location.in_front_of(2)
                    dest.y += 1
                    coords = (int(dest.x * 32) + 16, int(dest.y * 32) + 16,
                        int(dest.z * 32) + 16)
                    self.factory.give(coords, (primary, secondary), 1)

                    # Re-send inventory.
                    packet = self.player.inventory.save_to_packet()
                    self.transport.write(packet)

                    # If no items in this slot are left, this player isn't
                    # holding an item anymore.
                    if i.holdables[self.player.equipped] is None:
                        packet = make_packet("entity-equipment",
                            eid=self.player.eid,
                            slot=0,
                            primary=65535,
                            secondary=0
                        )
                        self.factory.broadcast_for_others(packet, self)
            return

        bigx, smallx, bigz, smallz = split_coords(container.x, container.z)
        coords = smallx, container.y, smallz

        try:
            chunk = self.chunks[bigx, bigz]
        except KeyError:
            self.error("Couldn't dig in chunk (%d, %d)!" % (bigx, bigz))
            return

        block = chunk.get_block((smallx, container.y, smallz))

        if container.state == "started":
            tool = self.player.inventory.holdables[self.player.equipped]
            # Check to see whether we should break this block.
            if self.dig_policy.is_1ko(block, tool):
                self.run_dig_hooks(chunk, coords, blocks[block])
            else:
                # Set up a timer for breaking the block later.
                dtime = time() + self.dig_policy.dig_time(block, tool)
                self.last_dig = coords, block, dtime
        elif container.state == "stopped":
            # The client thinks it has broken a block. We shall see.
            if not self.last_dig:
                return

            oldcoords, oldblock, dtime = self.last_dig
            if oldcoords != coords or oldblock != block:
                # Nope!
                self.last_dig = None
                return

            dtime -= time()

            # When enough time has elapsed, run the dig hooks.
            d = deferLater(reactor, max(dtime, 0), self.run_dig_hooks, chunk,
                           coords, blocks[block])
            d.addCallback(lambda none: setattr(self, "last_dig", None))

    def run_dig_hooks(self, chunk, coords, block):
        """
        Destroy a block and run the post-destroy dig hooks.
        """

        if block.breakable:
            chunk.destroy(coords)

        x, y, z = coords

        l = []
        for hook in self.dig_hooks:
            l.append(maybeDeferred(hook.dig_hook,
                                   self.factory, chunk, x, y, z, block))

        dl = DeferredList(l)
        dl.addCallback(lambda none: self.factory.flush_chunk(chunk))

    @inlineCallbacks
    def build(self, container):
        if container.x == -1 and container.z == -1 and container.y == 255:
            # Lala-land build packet. Discard it for now.
            return

        # Is the target being selected?
        bigx, smallx, bigz, smallz = split_coords(container.x, container.z)
        try:
            chunk = self.chunks[bigx, bigz]
        except KeyError:
            self.error("Couldn't select in chunk (%d, %d)!" % (bigx, bigz))
            return

        if (chunk.get_block((smallx, container.y, smallz)) ==
            blocks["workbench"].slot):
            i = Workbench()
            sync_inventories(self.player.inventory, i)
            self.windows[self.wid] = i
            packet = make_packet("window-open", wid=self.wid, type="workbench",
                title="Hurp", slots=2)
            self.wid += 1
            self.transport.write(packet)
            return

        # Ignore clients that think -1 is placeable.
        if container.primary == -1:
            return

        # Special case when face is "noop": Update the status of the currently
        # held block rather than placing a new block.
        if container.face == "noop":
            return

        if container.primary in blocks:
            block = blocks[container.primary]
        elif container.primary in items:
            block = items[container.primary]
        else:
            log.err("Ignoring request to place unknown block %d" %
                container.primary)
            return

        # it's the top of the world, you can't build here
        if container.y == 127 and container.face == '+y':
            return

        # Run pre-build hooks. These hooks are able to interrupt the build
        # process.
        builddata = BuildData(block, 0x0, container.x, container.y,
            container.z, container.face)

        for hook in self.pre_build_hooks:
            cont, builddata = yield maybeDeferred(hook.pre_build_hook,
                self.factory, self.player, builddata)
            if not cont:
                break

        # Run the build.
        try:
            yield maybeDeferred(self.run_build, builddata)
        except BuildError:
            return

        newblock = builddata.block.slot
        coords = builddata.x, builddata.y, builddata.z

        # Run post-build hooks. These are merely callbacks which cannot
        # interfere with the build process, largely because the build process
        # already happened.
        for hook in self.post_build_hooks:
            yield maybeDeferred(hook.post_build_hook, self.factory,
                self.player, coords, builddata.block)

        # Feed automatons.
        for automaton in self.factory.automatons:
            if newblock in automaton.blocks:
                automaton.feed(self.factory,
                    (builddata.x, builddata.y, builddata.z))

        # Re-send inventory.
        # XXX this could be optimized if/when inventories track damage.
        packet = self.player.inventory.save_to_packet()
        self.transport.write(packet)

        # Flush damaged chunks.
        for chunk in self.chunks.itervalues():
            self.factory.flush_chunk(chunk)

    def run_build(self, builddata):
        block, metadata, x, y, z, face = builddata

        # Don't place items as blocks.
        if block.slot not in blocks:
            raise BuildError("Couldn't build item %r as block" % block)

        # Check for orientable blocks.
        if not metadata and block.orientable():
            metadata = block.orientation(face)
            if metadata is None:
                # Oh, I guess we can't even place the block on this face.
                raise BuildError("Couldn't orient block %r on face %s" %
                    (block, face))

        # Make sure we can remove it from the inventory first.
        if not self.player.inventory.consume((block.slot, 0),
            self.player.equipped):
            # Okay, first one was a bust; maybe we can consume the related
            # block for dropping instead?
            if not self.player.inventory.consume((block.drop, 0),
                self.player.equipped):
                raise BuildError("Couldn't consume %r from inventory" % block)

        # Offset coords according to face.
        if face == "-x":
            x -= 1
        elif face == "+x":
            x += 1
        elif face == "-y":
            y -= 1
        elif face == "+y":
            y += 1
        elif face == "-z":
            z -= 1
        elif face == "+z":
            z += 1

        # Set the block and data.
        dl = [self.factory.world.set_block((x, y, z), block.slot)]
        if metadata:
            dl.append(self.factory.world.set_metadata((x, y, z), metadata))

        return DeferredList(dl)

    def equip(self, container):
        self.player.equipped = container.item

        # Inform everyone about the item the player is holding now.
        item = self.player.inventory.holdables[self.player.equipped]
        if item is None:
            # Empty slot. Use signed short -1 == unsigned 65535.
            primary, secondary = 65535, 0
        else:
            primary, secondary, count = item

        packet = make_packet("entity-equipment",
            eid=self.player.eid,
            slot=0,
            primary=primary,
            secondary=secondary
        )
        self.factory.broadcast_for_others(packet, self)

    def pickup(self, container):
        self.factory.give((container.x, container.y, container.z),
            (container.primary, container.secondary), container.count)

    def animate(self, container):
        # Broadcast the animation of the entity to everyone else. Only swing
        # arm is send by notchian clients.
        packet = make_packet("animate",
            eid=self.player.eid,
            animation=container.animation
        )
        self.factory.broadcast_for_others(packet, self)

    def wclose(self, container):
        if container.wid in self.windows:
            i = self.windows[container.wid]
            if i.identifier == 1:
                # Closing the workbench.
                dest = self.location.in_front_of(1)
                dest.y += 1
                coords = (int(dest.x * 32) + 16, int(dest.y * 32) + 16,
                    int(dest.z * 32) + 16)
                # loop over items left in workbench
                for item in i.crafting:
                    if item is None:
                        continue
                    self.factory.give(coords, (item[0], item[1]), item[2])
            del self.windows[container.wid]
            sync_inventories(i, self.player.inventory)
        elif container.wid == 0:
            pass
        else:
            self.error("Can't close non-existent window %d!" % container.wid)

    def waction(self, container):
        if container.wid == 0:
            # Inventory.
            i = self.player.inventory
        elif container.wid in self.windows:
            i = self.windows[container.wid]
        else:
            self.error("Couldn't find window %d" % container.wid)

        selected = i.select(container.slot, bool(container.button),
            bool(container.shift))

        if selected:
            # XXX should be if there's any damage to the inventory
            packet = i.save_to_packet()
            self.transport.write(packet)

            # Inform other players about changes to this player's equipment.
            if container.wid == 0 and (container.slot in range(5, 9) or
                                       container.slot == 36):

                # Armor changes.
                if container.slot in range(5, 9):
                    item = i.armor[container.slot - 5]
                    # Order of slots is reversed in the equipment package.
                    slot = 4 - (container.slot - 5)
                # Currently equipped item changes.
                elif container.slot == 36:
                    item = i.holdables[0]
                    slot = 0

                if item is None:
                    primary, secondary = 65535, 0
                else:
                    primary, secondary, count = item
                packet = make_packet("entity-equipment",
                    eid=self.player.eid,
                    slot=slot,
                    primary=primary,
                    secondary=secondary
                )
                self.factory.broadcast_for_others(packet, self)

        packet = make_packet("window-token", wid=0, token=container.token,
            acknowledged=selected)
        self.transport.write(packet)

    def sign(self, container):
        bigx, smallx, bigz, smallz = split_coords(container.x, container.z)

        try:
            chunk = self.chunks[bigx, bigz]
        except KeyError:
            self.error("Couldn't handle sign in chunk (%d, %d)!" % (bigx, bigz))
            return

        if (smallx, container.y, smallz) in chunk.tiles:
            new = False
            s = chunk.tiles[smallx, container.y, smallz]
        else:
            new = True
            s = Sign(smallx, container.y, smallz)
            chunk.tiles[smallx, container.y, smallz] = s

        s.text1 = container.line1
        s.text2 = container.line2
        s.text3 = container.line3
        s.text4 = container.line4

        chunk.dirty = True

        # The best part of a sign isn't making one, it's showing everybody
        # else on the server that you did.
        packet = make_packet("sign", container)
        self.factory.broadcast_for_chunk(packet, bigx, bigz)

        # Run sign hooks.
        for hook in self.sign_hooks:
            hook.sign_hook(self.factory, chunk, container.x, container.y,
                container.z, [s.text1, s.text2, s.text3, s.text4], new)

    def disable_chunk(self, x, z):
        # Remove the chunk from cache.
        chunk = self.chunks.pop(x, z)

        for entity in chunk.entities:
            packet = make_packet("destroy", eid=entity.eid)
            self.transport.write(packet)

        packet = make_packet("prechunk", x=x, z=z, enabled=0)
        self.transport.write(packet)

    def enable_chunk(self, x, z):
        """
        Request a chunk.

        This function will asynchronously obtain the chunk, and send it on the
        wire.

        :returns: `Deferred` that will be fired when the chunk is obtained,
                  with no arguments
        """

        if (x, z) in self.chunks:
            return succeed(None)

        d = self.factory.world.request_chunk(x, z)
        d.addCallback(self.send_chunk)

        return d

    def send_chunk(self, chunk):
        packet = make_packet("prechunk", x=chunk.x, z=chunk.z, enabled=1)
        self.transport.write(packet)

        packet = chunk.save_to_packet()
        self.transport.write(packet)

        for entity in chunk.entities:
            packet = entity.save_to_packet()
            self.transport.write(packet)

        for entity in chunk.tiles.itervalues():
            if entity.name == "Sign":
                packet = entity.save_to_packet()
                self.transport.write(packet)

        self.chunks[chunk.x, chunk.z] = chunk

    def send_initial_chunk_and_location(self):
        bigx, smallx, bigz, smallz = split_coords(self.location.x,
            self.location.z)

        # Spawn the 25 chunks in a square around the spawn, *before* spawning
        # the player. Otherwise, there's a funky Beta 1.2 bug which causes the
        # player to not be able to move.
        d = cooperate(
            self.enable_chunk(i, j)
            for i, j in product(
                xrange(bigx - 3, bigx + 3),
                xrange(bigz - 3, bigz + 3)
            )
        ).whenDone()

        # Don't dare send more chunks beyond the initial one until we've
        # spawned.
        d.addCallback(lambda none: self.update_location())
        d.addCallback(lambda none: self.position_changed())

        # Send the MOTD.
        if self.motd:
            packet = make_packet("chat",
                message=self.motd.replace("<tagline>", get_motd()))
            d.addCallback(lambda none: self.transport.write(packet))

        # Finally, start the secondary chunk loop.
        d.addCallback(lambda none: self.update_chunks())

    def update_location(self):
        bigx, smallx, bigz, smallz = split_coords(self.location.x,
            self.location.z)

        chunk = self.chunks[bigx, bigz]

        height = chunk.height_at(smallx, smallz) + 2
        self.location.y = height

        packet = self.location.save_to_packet()
        self.transport.write(packet)

    def update_chunks(self):
        x, chaff, z, chaff = split_coords(self.location.x, self.location.z)

        new = set((i + x, j + z) for i, j in circle)
        old = set(self.chunks.iterkeys())
        added = new - old
        discarded = old - new

        # Perhaps some explanation is in order.
        # The cooperate() function iterates over the iterable it is fed,
        # without tying up the reactor, by yielding after each iteration. The
        # inner part of the generator expression generates all of the chunks
        # around the currently needed chunk, and it sorts them by distance to
        # the current chunk. The end result is that we load chunks one-by-one,
        # nearest to furthest, without stalling other clients.
        if self.chunk_tasks:
            for task in self.chunk_tasks:
                try:
                    task.stop()
                except (TaskDone, TaskFailed):
                    pass

        self.chunk_tasks = [cooperate(task) for task in
            (
                self.enable_chunk(i, j) for i, j in
                sorted(added, key=lambda t: (t[0] - x)**2 + (t[1] - z)**2)
            ),
            (self.disable_chunk(i, j) for i, j in discarded)
        ]

    def update_time(self):
        packet = make_packet("time", timestamp=int(self.factory.time))
        self.transport.write(packet)

    def connectionLost(self, reason):
        if self.time_loop:
            self.time_loop.stop()

        if self.chunk_tasks:
            for task in self.chunk_tasks:
                try:
                    task.stop()
                except (TaskDone, TaskFailed):
                    pass

        if self.player:
            self.factory.world.save_player(self.username, self.player)
            self.factory.destroy_entity(self.player)
            packet = make_packet("destroy", eid=self.player.eid)
            self.factory.broadcast(packet)
            self.factory.chat("%s has left the game." % self.username)

        if self.username in self.factory.protocols:
            del self.factory.protocols[self.username]
