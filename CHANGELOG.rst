1.5
===

Features
--------

* Added web plugin support

  * Added worldmap plugin for viewing the spawn area

* Introduced automatons

  * Ported fluids (water, lava) to the automaton interface
  * Created a tree automaton to turn saplings into trees

* Created policies for digging

  * Notchy dig policy mimics Notchian server dig times
  * Speedy dig policy allows instant digging of blocks

* Removed "Replace" dig hook with builtin functionality
* Added more block and item names, and created names for wool and dye types
* Added support for wolves
* Rewrote most of the /time command to support setting the day, time, season,
  and time of day
* Added /ascend and /descend commands
* Allowed chat commands to be asynchronous if necessary

Bugfixes
--------

* Fixed several crashes/hangs in Ampoule support
* Made factory startup messages show up in log
* Fixed several bugs in item saving and chunk saving which made
  Bravo-generated worlds incompatible with Notchian worlds
* Fixed bug in sapling generator causing too many saplings to be placed
* Fixed bug in sapling generator where saplings could be spawned on beaches
* Fixed a few edge-case bugs in water automaton where water would not spread
* Fixed a few previously uncraftable recipes

1.4
===

* Started keeping a changelog
* Created a separate license file
* Introduced Exocet for improved plugin loading

  * Plugins now are reloadable
  * Plugins may not import insecure modules

* Many myriad documentation improvements and expansions
* Support for protocols 11

  * Protocol 10 support is completely gone now. As with older protocols,
    contact me if you actually need old protocol support.

* Improved block metadata representations and fixes
* Chunk improvements

  * Massively improved chunk lighting algorithms
  * Chunks now have lighting tests
  * Chunks now illuminate themselves correctly
  * Out-of-bounds accesses on chunks now warn instead of raise

* Entity improvements

  * Support for paintings
  * Support for peaceful mobs: Cows, chucks, pigs, squid, sheep
  * Support for aggressive mobs: Slimes
  * Support for music

* World improvements

  * Worlds are now fully asynchronous

* Interface changes

  * IRecipes now check their sizes
  * ISerializers may return Deferreds in all of their actions
  * IBuildHooks may return Deferreds
  * Introduced IUseHook

* Introduced MOTD support
* Refactored packet module into package
* Rewrote /help
* Rewrote "caves" terrain generator
* Introduced "trees" terrain generator
* Fixed several bugs in fluid simulator
* Fixed several broken recipes: TNT, ladders, shovels, fishing rods
* Fixed bug with snow on Notchian server geometry
* Introduced web service
