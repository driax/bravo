from twisted.plugin import IPlugin
from zope.interface import implements

from beta.ibeta import ICommand
from beta.plugin import retrieve_plugins

class Help(object):

    implements(IPlugin, ICommand)

    def dispatch(self, factory, parameters):
        plugins = retrieve_plugins(ICommand)
        for name, plugin in plugins.iteritems():
            print "%s" % plugin.name

    name = "help"

class List(object):

    implements(IPlugin, ICommand)

    def dispatch(self, factory, parameters):
        for player in factory.players:
            print "%s" % player

    name = "list"


help = Help()
list = List()
