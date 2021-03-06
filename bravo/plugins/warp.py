import csv
from StringIO import StringIO

from twisted.internet.defer import inlineCallbacks, returnValue
from zope.interface import implements

from bravo.ibravo import IChatCommand, IConsoleCommand
from bravo.utilities.coords import split_coords

csv.register_dialect("hey0", delimiter=":")

def get_locations(data):
    d = {}
    for line in csv.reader(StringIO(data), dialect="hey0"):
        name, x, y, z, yaw, pitch = line[:6]
        x = float(x)
        y = float(y)
        z = float(z)
        yaw = float(yaw)
        pitch = float(pitch)
        d[name] = (x, y, z, yaw, pitch)
    return d

def put_locations(d):
    data = StringIO()
    writer = csv.writer(data, dialect="hey0")
    for name, stuff in d.iteritems():
        writer.writerow([name] + list(stuff))
    return data.getvalue()

class Home(object):

    implements(IChatCommand, IConsoleCommand)

    def chat_command(self, factory, username, parameters):
        data = factory.world.serializer.load_plugin_data("homes")
        homes = get_locations(data)

        protocol = factory.protocols[username]
        l = protocol.player.location
        if username in homes:
            yield "Teleporting %s home" % username
            (l.x, l.y, l.z, l.yaw, l.pitch) = homes[username]
        else:
            yield "Teleporting %s to spawn" % username
            l.x, l.y, l.z = factory.world.spawn
            l.yaw, l.pitch = 0, 0
        protocol.send_initial_chunk_and_location()
        yield "Teleportation successful!"

    def console_command(self, factory, parameters):
        for i in self.chat_command(factory, parameters[0], parameters[1:]):
            yield i

    name = "home"
    aliases = tuple()
    usage = ""
    info = "Warps player home"

class SetHome(object):

    implements(IChatCommand)

    def chat_command(self, factory, username, parameters):
        yield "Saving %s's home..." % username

        protocol = factory.protocols[username]
        x = protocol.player.location.x
        y = protocol.player.location.y
        z = protocol.player.location.z
        yaw = protocol.player.location.yaw
        pitch = protocol.player.location.pitch

        data = factory.world.serializer.load_plugin_data("homes")
        d = get_locations(data)
        d[username] = x, y, z, yaw, pitch
        data = put_locations(d)
        factory.world.serializer.save_plugin_data("homes", data)

        yield "Saved %s!" % username

    name = "sethome"
    aliases = tuple()
    usage = ""
    info = "Set home"

class Warp(object):

    implements(IChatCommand, IConsoleCommand)

    def chat_command(self, factory, username, parameters):
        data = factory.world.serializer.load_plugin_data("warps")
        warps = get_locations(data)

        location = parameters[0]
        if location in warps:
            yield "Teleporting you to %s" % location
            protocol = factory.protocols[username]
            # An explanation might be necessary.
            # We are changing the location of the player, but we must
            # immediately send a new location packet in order to force the
            # player to appear at the new location. However, before we can do
            # that, we need to get the chunk loaded for them. This ends up
            # being the same sequence of events as the initial chunk and
            # location setup, so we call send_initial_chunk_and_location()
            # instead of update_location().
            l = protocol.player.location
            (l.x, l.y, l.z, l.yaw, l.pitch) = warps[location]
            protocol.send_initial_chunk_and_location()
            yield "Teleportation successful!"
        else:
            yield "No warp location %s available" % parameters

    def console_command(self, factory, parameters):
        for i in self.chat_command(factory, parameters[0], parameters[1:]):
            yield i

    name = "warp"
    aliases = tuple()
    usage = "<location>"
    info = "Warps player to a location"

class ListWarps(object):

    implements(IChatCommand, IConsoleCommand)

    def dispatch(self, factory):
        data = factory.world.serializer.load_plugin_data("warps")
        warps = get_locations(data)

        if warps:
            yield "Warp locations:"
            for key in sorted(warps.iterkeys()):
                yield "~ %s" % key
        else:
            yield "No warps are set!"

    def chat_command(self, factory, username, parameters):
        for i in self.dispatch(factory):
            yield i

    def console_command(self, factory, parameters):
        for i in self.dispatch(factory):
            yield i

    name = "listwarps"
    aliases = tuple()
    usage = ""
    info = "List warps"

class SetWarp(object):

    implements(IChatCommand)

    def chat_command(self, factory, username, parameters):
        name = "".join(parameters)

        yield "Saving warp %s..." % name

        protocol = factory.protocols[username]
        x = protocol.player.location.x
        y = protocol.player.location.y
        z = protocol.player.location.z
        yaw = protocol.player.location.yaw
        pitch = protocol.player.location.pitch

        data = factory.world.serializer.load_plugin_data("warps")
        d = get_locations(data)
        d[name] = x, y, z, yaw, pitch
        data = put_locations(d)
        factory.world.serializer.save_plugin_data("warps", data)

        yield "Saved %s!" % name

    name = "setwarp"
    aliases = tuple()
    usage = "<name>"
    info = "Set warp"

class RemoveWarp(object):

    implements(IChatCommand)

    def chat_command(self, factory, username, parameters):
        name = "".join(parameters)

        yield "Removing warp %s..." % name

        data = factory.world.serializer.load_plugin_data("warps")
        d = get_locations(data)
        if name in d:
            del d[name]
            yield "Saving warps..."
            data = put_locations(d)
            factory.world.serializer.save_plugin_data("warps", data)
            yield "Removed %s!" % name
        else:
            yield "No such warp %s!" % name

    name = "removewarp"
    aliases = tuple()
    usage = "<name>"
    info = "Remove warp"

class Ascend(object):

    implements(IChatCommand)

    @inlineCallbacks
    def chat_command(self, factory, username, parameters):
        protocol = factory.protocols[username]
        x = protocol.player.location.x
        z = protocol.player.location.z
        bigx, smallx, bigz, smallz = split_coords(x, z)

        chunk = yield factory.world.request_chunk(bigx, bigz)
        column = chunk.get_column(smallx, smallz)

        y = protocol.player.location.y

        # Find the next spot above us which has a platform and two empty
        # blocks of air.
        while y < 125:
            y += 1
            if column[y] and not column[y + 1] and not column[y + 2]:
                break
        else:
            returnValue(("Couldn't find anywhere to ascend!",))

        protocol.player.location.y = y
        protocol.send_initial_chunk_and_location()
        returnValue(("Ascended!",))

    name = "ascend"
    aliases = tuple()
    usage = ""
    info = "Ascend to a higher Y-level"

class Descend(object):

    implements(IChatCommand)

    @inlineCallbacks
    def chat_command(self, factory, username, parameters):
        protocol = factory.protocols[username]
        x = protocol.player.location.x
        z = protocol.player.location.z
        bigx, smallx, bigz, smallz = split_coords(x, z)

        chunk = yield factory.world.request_chunk(bigx, bigz)
        column = chunk.get_column(smallx, smallz)

        y = protocol.player.location.y

        # Find the next spot below us which has a platform and two empty
        # blocks of air.
        while y > 0:
            y -= 1
            if column[y] and not column[y + 1] and not column[y + 2]:
                break
        else:
            returnValue(("Couldn't find anywhere to descend!",))

        protocol.player.location.y = y
        protocol.send_initial_chunk_and_location()
        returnValue(("Descended!",))

    name = "descend"
    aliases = tuple()
    usage = ""
    info = "Descend to a lower Y-level"

home = Home()
sethome = SetHome()
warp = Warp()
listwarps = ListWarps()
setwarp = SetWarp()
removewarp = RemoveWarp()
ascend = Ascend()
descend = Descend()
