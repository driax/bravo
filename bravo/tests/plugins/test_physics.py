from twisted.trial import unittest

import shutil
import tempfile

from numpy.testing import assert_array_equal

from twisted.internet.defer import inlineCallbacks

import bravo.blocks
import bravo.config
from bravo.ibravo import IDigHook
import bravo.plugin
import bravo.world

class PhysicsMockFactory(object):

    def flush_chunk(self, chunk):
        pass

class TestWater(unittest.TestCase):

    def setUp(self):
        # Using dig hook to grab the plugin since the build hook was nuked in
        # favor of the automaton interface.
        self.p = bravo.plugin.retrieve_plugins(IDigHook)

        if "water" not in self.p:
            raise unittest.SkipTest("Plugin not present")

        self.hook = self.p["water"]

        # Set up world.
        self.name = "unittest"
        self.d = tempfile.mkdtemp()

        bravo.config.configuration.add_section("world unittest")
        bravo.config.configuration.set("world unittest", "url",
            "file://%s" % self.d)
        bravo.config.configuration.set("world unittest", "serializer",
            "alpha")

        self.w = bravo.world.World(self.name)
        self.w.pipeline = []

        # And finally the mock factory.
        self.f = PhysicsMockFactory()
        self.f.world = self.w

    def tearDown(self):
        if self.w.chunk_management_loop.running:
            self.w.chunk_management_loop.stop()
        del self.w

        shutil.rmtree(self.d)
        bravo.config.configuration.remove_section("world unittest")

    def test_trivial(self):
        pass

    def test_zero_y(self):
        """
        Double-check that water placed on the very bottom of the world doesn't
        cause internal errors.
        """

        self.w.set_block((0, 0, 0), bravo.blocks.blocks["spring"].slot)
        self.hook.pending[self.f].add((0, 0, 0))

        # Tight-loop run the hook to equilibrium; if any exceptions happen,
        # they will bubble up.
        while self.hook.pending:
            self.hook.process()

    @inlineCallbacks
    def test_spring_spread(self):
        self.w.set_block((0, 0, 0), bravo.blocks.blocks["spring"].slot)
        self.hook.pending[self.f].add((0, 0, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        for coords in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)):
            block = yield self.w.get_block(coords)
            metadata = yield self.w.get_metadata(coords)
            self.assertEqual(block, bravo.blocks.blocks["water"].slot)
            self.assertEqual(metadata, 0x0)

    @inlineCallbacks
    def test_spring_fall(self):
        """
        Falling water should appear below springs.
        """

        self.w.set_block((0, 1, 0), bravo.blocks.blocks["spring"].slot)
        self.hook.pending[self.f].add((0, 1, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        block = yield self.w.get_block((0, 0, 0))
        metadata = yield self.w.get_metadata((0, 0, 0))
        self.assertEqual(block, bravo.blocks.blocks["water"].slot)
        self.assertEqual(metadata, 0x8)

    @inlineCallbacks
    def test_spring_fall_dig(self):
        """
        Destroying ground underneath spring should allow water to continue
        falling downwards.
        """

        self.w.set_block((0, 1, 0), bravo.blocks.blocks["spring"].slot)
        self.w.set_block((0, 0, 0), bravo.blocks.blocks["dirt"].slot)
        self.hook.pending[self.f].add((0, 1, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        #dig away dirt under spring
        self.w.destroy((0, 0, 0))
        self.hook.pending[self.f].add((0, 1, 0))

        while self.hook.pending:
            self.hook.process()

        block = yield self.w.get_block((0, 0, 0))
        self.assertEqual(block, bravo.blocks.blocks["water"].slot)

    @inlineCallbacks
    def test_spring_fall_dig_offset(self):
        """
        Destroying ground next to a spring should cause a waterfall effect.
        """

        self.w.set_block((0, 1, 0), bravo.blocks.blocks["spring"].slot)
        self.w.set_block((0, 0, 0), bravo.blocks.blocks["dirt"].slot)
        self.w.set_block((0, 0, 1), bravo.blocks.blocks["dirt"].slot)
        self.hook.pending[self.f].add((0, 1, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        # Dig away the dirt next to the dirt under the spring, and simulate
        # the dig hook by adding the block above it.
        self.w.destroy((0, 0, 1))
        self.hook.pending[self.f].add((0, 1, 1))

        while self.hook.pending:
            self.hook.process()

        block = yield self.w.get_block((0, 0, 1))
        self.assertEqual(block, bravo.blocks.blocks["water"].slot)

    @inlineCallbacks
    def test_spring_waterfall(self):
        """
        Fluid should not spread across existing fluid.
        """

        self.w.set_block((0, 3, 0), bravo.blocks.blocks["spring"].slot)
        self.w.set_block((0, 2, 0), bravo.blocks.blocks["dirt"].slot)
        self.hook.pending[self.f].add((0, 1, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        #dig away dirt and add known spring and fluid blocks
        self.w.destroy((0, 2, 0))
        self.hook.pending[self.f].add((0, 2, 1))
        self.hook.pending[self.f].add((0, 2, -1))
        self.hook.pending[self.f].add((0, 3, 0))
        self.hook.pending[self.f].add((1, 2, 0))
        self.hook.pending[self.f].add((-1, 2, 0))

        while self.hook.pending:
            self.hook.process()

        block = yield self.w.get_block((0, 1, 2))
        self.assertEqual(block, bravo.blocks.blocks["air"].slot)


    @inlineCallbacks
    def test_obstacle(self):
        """
        Test that obstacles are flowed around correctly.
        """

        yield self.w.set_block((0, 0, 0), bravo.blocks.blocks["spring"].slot)
        yield self.w.set_block((1, 0, 0), bravo.blocks.blocks["stone"].slot)
        self.hook.pending[self.f].add((0, 0, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        # Make sure that the water level behind the stone is 0x3, not 0x0.
        metadata = yield self.w.get_metadata((2, 0, 0))
        self.assertEqual(metadata, 0x3)

    @inlineCallbacks
    def test_sponge(self):
        """
        Test that sponges prevent water from spreading near them.
        """

        self.w.set_block((0, 0, 0), bravo.blocks.blocks["spring"].slot)
        self.w.set_block((3, 0, 0), bravo.blocks.blocks["sponge"].slot)
        self.hook.pending[self.f].add((0, 0, 0))
        self.hook.pending[self.f].add((3, 0, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        # Make sure that water did not spread near the sponge.
        block = yield self.w.get_block((1, 0, 0))
        self.assertNotEqual(block, bravo.blocks.blocks["water"].slot)

    @inlineCallbacks
    def test_sponge_absorb_spring(self):
        """
        Test that sponges can absorb springs and will cause all of the
        surrounding water to dry up.
        """

        self.w.set_block((0, 0, 0), bravo.blocks.blocks["spring"].slot)
        self.hook.pending[self.f].add((0, 0, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        self.w.set_block((1, 0, 0), bravo.blocks.blocks["sponge"].slot)
        self.hook.pending[self.f].add((1, 0, 0))

        while self.hook.pending:
            self.hook.process()

        for coords in ((0, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)):
            block = yield self.w.get_block(coords)
            self.assertEqual(block, bravo.blocks.blocks["air"].slot)

        # Make sure that water did not spread near the sponge.
        block = yield self.w.get_block((1, 0, 0))
        self.assertNotEqual(block, bravo.blocks.blocks["water"].slot)

    @inlineCallbacks
    def test_sponge_salt(self):
        """
        Test that sponges don't "salt the earth" or have any kind of lasting
        effects after destruction.
        """

        self.w.set_block((0, 0, 0), bravo.blocks.blocks["spring"].slot)
        self.hook.pending[self.f].add((0, 0, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        # Take a snapshot.
        chunk = yield self.w.request_chunk(0, 0)
        before = chunk.blocks[:, :, 0], chunk.metadata[:, :, 0]

        self.w.set_block((3, 0, 0), bravo.blocks.blocks["sponge"].slot)
        self.hook.pending[self.f].add((3, 0, 0))

        while self.hook.pending:
            self.hook.process()

        self.w.destroy((3, 0, 0))
        self.hook.pending[self.f].add((3, 0, 0))

        while self.hook.pending:
            self.hook.process()

        after = chunk.blocks[:, :, 0], chunk.metadata[:, :, 0]

        # Make sure that the sponge didn't permanently change anything.
        assert_array_equal(before, after)

    @inlineCallbacks
    def test_spring_remove(self):
        """
        Test that water dries up if no spring is providing it.
        """

        self.w.set_block((0, 0, 0), bravo.blocks.blocks["spring"].slot)
        self.hook.pending[self.f].add((0, 0, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        # Remove the spring.
        self.w.destroy((0, 0, 0))
        self.hook.pending[self.f].add((0, 0, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        for coords in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)):
            block = yield self.w.get_block(coords)
            self.assertEqual(block, bravo.blocks.blocks["air"].slot)

    @inlineCallbacks
    def test_spring_underneath_keepalive(self):
        """
        Test that springs located at a lower altitude than stray water do not
        keep that stray water alive.
        """

        self.w.set_block((0, 0, 0), bravo.blocks.blocks["spring"].slot)
        self.w.set_block((0, 1, 0), bravo.blocks.blocks["spring"].slot)
        self.hook.pending[self.f].add((0, 0, 0))
        self.hook.pending[self.f].add((0, 1, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        # Remove the upper spring.
        self.w.destroy((0, 1, 0))
        self.hook.pending[self.f].add((0, 1, 0))

        # Tight-loop run the hook to equilibrium.
        while self.hook.pending:
            self.hook.process()

        # Check that the upper water blocks dried out. Don't care about the
        # lower ones in this test.
        for coords in ((1, 1, 0), (-1, 1, 0), (0, 1, 1), (0, 1, -1)):
            block = yield self.w.get_block(coords)
            self.assertEqual(block, bravo.blocks.blocks["air"].slot)

class TestRedstone(unittest.TestCase):

    def setUp(self):
        self.p = bravo.plugin.retrieve_plugins(IDigHook)

        if "redstone" not in self.p:
            raise unittest.SkipTest("Plugin not present")

        self.hook = self.p["redstone"]

        # Set up world.
        self.name = "unittest"
        self.d = tempfile.mkdtemp()

        bravo.config.configuration.add_section("world unittest")
        bravo.config.configuration.set("world unittest", "url",
            "file://%s" % self.d)
        bravo.config.configuration.set("world unittest", "serializer",
            "alpha")

        self.w = bravo.world.World(self.name)
        self.w.pipeline = []

        # And finally the mock factory.
        self.f = PhysicsMockFactory()
        self.f.world = self.w

    def tearDown(self):
        if self.w.chunk_management_loop.running:
            self.w.chunk_management_loop.stop()
        del self.w

        shutil.rmtree(self.d)
        bravo.config.configuration.remove_section("world unittest")

    def test_trivial(self):
        pass

    @inlineCallbacks
    def test_update_wires_enable(self):
        for i in range(16):
            self.w.set_block((i, 0, 0),
                bravo.blocks.blocks["redstone-wire"].slot)
            self.w.set_metadata((i, 0, 0), 0x0)

        # Enable wires.
        self.hook.update_wires(self.f, 0, 0, 0, True)

        for i in range(16):
            metadata = yield self.w.get_metadata((i, 0, 0))
            self.assertEqual(metadata, 0xf - i)

    @inlineCallbacks
    def test_update_wires_disable(self):
        for i in range(16):
            self.w.set_block((i, 0, 0),
                bravo.blocks.blocks["redstone-wire"].slot)
            self.w.set_metadata((i, 0, 0), i)

        # Disable wires.
        self.hook.update_wires(self.f, 0, 0, 0, False)

        for i in range(16):
            metadata = yield self.w.get_metadata((i, 0, 0))
            self.assertEqual(metadata, 0x0)
