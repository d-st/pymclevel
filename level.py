'''
Created on Jul 22, 2011

@author: Rio
'''


from mclevelbase import *
import tempfile
from collections import defaultdict

class MCLevel(object):
    """ MCLevel is an abstract class providing many routines to the different level types, 
    including a common copyEntitiesFrom built on class-specific routines, and
    a dummy getChunk/allChunks for the finite levels.
    
    MCLevel also provides compress and decompress methods that are used to load
    NBT format levels, and expects subclasses to override shapeChunkData to 
    assign a shape to the Blocks and other arrays. The resulting arrays after 
    reshape must be indexed [x,z,y]
    
    MCLevel subclasses must have Width, Length, and Height attributes.  The first two are always zero for infinite levels.
    Subclasses must also have Blocks, and optionally Data and BlockLight.
    """

    ###common to Creative, Survival and Indev. these routines assume
    ###self has Width, Height, Length, and Blocks

    materials = classicMaterials;
    isInfinite = False

    compressedTag = None
    root_tag = None

    Height = None
    Length = None
    Width = None

    players = ["Player"]
    dimNo = 0;
    parentWorld = None
    world = None
    @classmethod
    def isLevel(cls, filename):
        """Tries to find out whether the given filename can be loaded
        by this class.  Returns True or False.
        
        Subclasses should implement _isLevel, _isDataLevel, or _isTagLevel.
        """
        if hasattr(cls, "_isLevel"):
            return cls._isLevel(filename);

        with file(filename) as f:
            data = f.read();

        if hasattr(cls, "_isDataLevel"):
            return cls._isDataLevel(data);

        if hasattr(cls, "_isTagLevel"):
            try:
                root_tag = nbt.load(filename, data)
            except:
                return False;

            return cls._isTagLevel(root_tag);

        return False

    def getWorldBounds(self):
        return BoundingBox((0, 0, 0), self.size)

    @property
    def displayName(self):
        return os.path.basename(self.filename)

    @property
    def size(self):
        "Returns the level's dimensions as a tuple (X,Y,Z)"
        return (self.Width, self.Height, self.Length)

    @property
    def bounds(self):
        return BoundingBox((0, 0, 0), self.size)


    def close(self): pass

    # --- Compression ---
    def compress(self): pass
    def decompress(self):pass


    # --- Entity Methods ---
    def addEntity(self, entityTag): pass
    def addEntities(self, entities): pass
    def tileEntityAt(self, x, y, z): return None
    def addTileEntity(self, entityTag): pass
    def getEntitiesInBox(self, box): return []
    def getTileEntitiesInBox(self, box): return []
    def copyEntitiesFromIter(self, *args, **kw): yield;

    # --- Chunked Format Emulation ---
    def compressChunk(self, cx, cz): pass

    @property
    def loadedChunks(self):
        return itertools.product(xrange(0, self.Width + 15 >> 4), xrange(0, self.Length + 15 >> 4))

    @property
    def chunkCount(self):
        return (self.Width + 15 >> 4) * (self.Length + 15 >> 4)

    @property
    def allChunks(self):
        """Returns a synthetic list of chunk positions (xPos, zPos), to fake 
        being a chunked level format."""
        return self.loadedChunks


    def getChunks(self, chunks=None):
        """ pass a list of chunk coordinate tuples to get an iterator yielding
        InfdevChunks. pass nothing for an iterator of every chunk in the level. 
        the chunks are automatically loaded."""
        if chunks is None: chunks = self.allChunks;
        return (self.getChunk(cx, cz) for (cx, cz) in chunks if self.containsChunk(cx, cz))


    def _getFakeChunkEntities(self, cx, cz):
        """Returns Entities, TileEntities"""
        return ([], [])

    def getChunk(self, cx, cz):
        """Synthesize a FakeChunk object representing the chunk at the given
        position. Subclasses override fakeBlocksForChunk and fakeDataForChunk
        to fill in the chunk arrays"""

        class FakeChunk(EntityLevel):
            def load(self):pass
            def compress(self):pass
            def __init__(self):pass
            def chunkChanged(self):pass
            @property
            def materials(self): return self.world.materials

        f = FakeChunk()
        f.world = self;
        f.chunkPosition = (cx, cz)

        f.Blocks = self.fakeBlocksForChunk(cx, cz)


        f.Data = self.fakeDataForChunk(cx, cz)

        whiteLight = zeros_like(f.Blocks);
        whiteLight[:] = 15;

        f.BlockLight = whiteLight
        f.SkyLight = whiteLight

        f.Entities, f.TileEntities = self._getFakeChunkEntities(cx, cz)



        f.root_tag = TAG_Compound();

        return f

    def getAllChunkSlices(self):
        slices = (slice(None), slice(None), slice(None),)
        box = self.bounds
        x, y, z = box.origin

        for cpos in self.allChunks:
            xPos, zPos = cpos
            try:
                chunk = self.getChunk(xPos, zPos)
            except (ChunkMalformed, ChunkNotPresent):
                continue


            yield (chunk, slices, (xPos * 16 - x, 0, zPos * 16 - z))

    def getChunkSlicesCreating(self, box): 
        return self.getChunkSlices(box, create=True)
        
    def getChunkSlices(self, box, create=False):
        """ call this method to iterate through a large slice of the world by 
            visiting each chunk and indexing its data with a subslice.
        
        this returns an iterator, which yields 3-tuples containing:
        +  an InfdevChunk object, 
        +  a x,z,y triplet of slices that can be used to index the InfdevChunk's data arrays, 
        +  a x,y,z triplet representing the relative location of this subslice within the requested world slice.
        
        Note the different order of the coordinates between the 'slices' triplet
        and the 'offset' triplet. x,z,y ordering is used only
        to index arrays, since it reflects the order of the blocks in memory.
        In all other places, including an entity's 'Pos', the order is x,y,z. 
        
        'create' controls whether to create absent chunks or skip over them.
        create can be False, True, or a function of (slices, point) 
        """
        level = self

        #when yielding slices of chunks on the edge of the box, adjust the 
        #slices by an offset
        minxoff, minzoff = box.minx - (box.mincx << 4), box.minz - (box.mincz << 4);
        maxxoff, maxzoff = box.maxx - (box.maxcx << 4) + 16, box.maxz - (box.maxcz << 4) + 16;

        newMinY = 0
        if box.miny < 0:
            newMinY = -box.miny
        miny = max(0, box.miny)
        maxy = min(self.Height, box.maxy)

        for cx in range(box.mincx, box.maxcx):
            localMinX = 0
            localMaxX = 16
            if cx == box.mincx:
                localMinX = minxoff

            if cx == box.maxcx - 1:
                localMaxX = maxxoff
            newMinX = localMinX + (cx << 4) - box.minx
            newMaxX = localMaxX + (cx << 4) - box.minx


            for cz in range(box.mincz, box.maxcz):
                localMinZ = 0
                localMaxZ = 16
                if cz == box.mincz:
                    localMinZ = minzoff
                if cz == box.maxcz - 1:
                    localMaxZ = maxzoff
                newMinZ = localMinZ + (cz << 4) - box.minz
                newMaxZ = localMaxZ + (cz << 4) - box.minz
                slices, point = (
                    (slice(localMinX, localMaxX), slice(localMinZ, localMaxZ), slice(miny, maxy)),
                    (newMinX, newMinY, newMinZ)
                )
                
                if not level.containsChunk(cx,cz):
                    if create and hasattr(level, 'createChunk'):
                        if not hasattr(create, "__call__") or create(slices, point):
                            level.createChunk(cx,cz)
                        else:
                            continue
                    else:
                        continue;
                chunk = level.getChunk(cx,cz)
                yield chunk, slices, point


    def containsPoint(self, x, y, z):
        return (x >= 0 and x < self.Width and
                y >= 0 and y < self.Height and
                z >= 0 and z < self.Length)

    def containsChunk(self, cx, cz):
        #w+15 to allow non 16 aligned schematics
        return (cx >= 0 and cx < (self.Width + 15 >> 4) and
                cz >= 0 and cz < (self.Length + 15 >> 4))

    def chunkIsLoaded(self, cx, cz):
        return self.containsChunk(cx, cz)

    def chunkIsCompressed(self, cx, cz):
        return False

    def chunkIsDirty(self, cx, cz):
        return True


    def fakeBlocksForChunk(self, cx, cz):
        #return a 16x16xH block array for rendering.  Alpha levels can
        #just return the chunk data.  other levels need to reorder the
        #indices and return a slice of the blocks.

        cxOff = cx << 4
        czOff = cz << 4
        b = self.Blocks[cxOff:cxOff + 16, czOff:czOff + 16, 0:self.Height, ];
        #(w, l, h) = b.shape
        #if w<16 or l<16:
        #    b = resize(b, (16,16,h) )
        return b;

    def fakeDataForChunk(self, cx, cz):
        #Data is emulated for flexibility
        cxOff = cx << 4
        czOff = cz << 4

        if hasattr(self, "Data"):
            return self.Data[cxOff:cxOff + 16, czOff:czOff + 16, 0:self.Height, ];

        else:
            return zeros(shape=(16, 16, self.Height), dtype='uint8')

    # --- Block accessors ---
    def skylightAt(self, *args):
        return 15

    def setSkylightAt(self, *args): pass

    def setBlockDataAt(self, x, y, z, newdata): pass

    def blockDataAt(self, x, y, z): return 0;

    def blockLightAt(self, x, y, z): return 15;

    def blockAt(self, x, y, z):
        if x < 0 or y < 0 or z < 0: return 0
        if x >= self.Width or y >= self.Height or z >= self.Length: return 0;
        return self.Blocks[x, z, y]

    def setBlockAt(self, x, y, z, blockID):
        if x < 0 or y < 0 or z < 0: return 0
        if x >= self.Width or y >= self.Height or z >= self.Length: return 0;
        self.Blocks[x, z, y] = blockID

    # --- Fill and Replace ---
    def blockReplaceTable(self, blocksToReplace):
        blocktable = zeros((256, 16), dtype='bool')
        for b in blocksToReplace:
            if b.hasAlternate:
                blocktable[b.ID, b.blockData] = True
            else:
                blocktable[b.ID] = True

        return blocktable

    def fillBlocks(self, box, blockInfo, blocksToReplace=[]):

        if box is None:
            box = self.bounds
        else:
            box = box.intersect(self.bounds)

        info(u"Filling blocks in {0} with {1}, replacing{2}".format(box, blockInfo, blocksToReplace))

        slices = map(slice, box.origin, box.maximum)

        blocks = self.Blocks[slices[0], slices[2], slices[1]]
        if len(blocksToReplace):
            blocktable = self.blockReplaceTable(blocksToReplace)

            if hasattr(self, "Data"):
                data = self.Data[slices[0], slices[2], slices[1]]
                mask = blocktable[blocks, data]

                data[mask] = blockInfo.blockData;
            else:
                mask = blocktable[blocks, 0]

            blocks[mask] = blockInfo.ID;

        else:
            blocks[:] = blockInfo.ID;
            if hasattr(self, "Data"):
                self.Data[slices[0], slices[2], slices[1]] = blockInfo.blockData;


    # --- Transformations ---
    def rotateLeft(self):
        self.Blocks = swapaxes(self.Blocks, 1, 0)[:, ::-1, :]; #x=z; z=-x
        pass;

    def roll(self):
        self.Blocks = swapaxes(self.Blocks, 2, 0)[:, :, ::-1]; #x=y; y=-x
        pass

    def flipVertical(self):
        self.Blocks = self.Blocks[:, :, ::-1]; #y=-y
        pass

    def flipNorthSouth(self):
        self.Blocks = self.Blocks[::-1, :, :]; #x=-x
        pass

    def flipEastWest(self):
        self.Blocks = self.Blocks[:, ::-1, :]; #z=-z
        pass

    # --- Copying ---

    def copyBlocksFromFiniteToFinite(self, sourceLevel, sourceBox, destinationPoint, blocksToCopy):
        # assume destinationPoint is entirely within this level, and the size of sourceBox fits entirely within it.
        sourcex, sourcey, sourcez = map(slice, sourceBox.origin, sourceBox.maximum)
        destCorner2 = map(lambda a, b:a + b, sourceBox.size, destinationPoint)
        destx, desty, destz = map(slice, destinationPoint, destCorner2)

        sourceData = None
        if hasattr(sourceLevel, 'Data'):
            sourceData = sourceLevel.Data[sourcex, sourcez, sourcey]
        convertedSourceBlocks, convertedSourceData = self.convertBlocksFromLevel(sourceLevel, sourceLevel.Blocks[sourcex, sourcez, sourcey], sourceData)


        blocks = self.Blocks[destx, destz, desty]

        mask = slice(None, None)

        if not (blocksToCopy is None):
            typemask = zeros((256) , dtype='bool')
            typemask[blocksToCopy] = True;
            mask = typemask[convertedSourceBlocks]

        blocks[mask] = convertedSourceBlocks[mask]
        if hasattr(self, 'Data'):
            data = self.Data[destx, destz, desty]
            data[mask] = convertedSourceData[mask]


    def copyBlocksFromInfinite(self, sourceLevel, sourceBox, destinationPoint, blocksToCopy):

        if blocksToCopy is not None:
            typemask = zeros((256) , dtype='bool')
            typemask[blocksToCopy] = True;

        for (chunk, slices, point) in sourceLevel.getChunkSlices(sourceBox):
            point = map(lambda a, b:a + b, point, destinationPoint)
            point = point[0], point[2], point[1]
            mask = slice(None, None)

            convertedSourceBlocks, convertedSourceData = self.convertBlocksFromLevel(sourceLevel, chunk.Blocks[slices], chunk.Data[slices])


            destSlices = [slice(p, p + s.stop - s.start) for p, s in zip(point, slices) ]

            blocks = self.Blocks[ destSlices ];

            if blocksToCopy is not None:
                mask = typemask[convertedSourceBlocks]

            blocks[mask] = convertedSourceBlocks[mask]

            if hasattr(self, 'Data'):
                data = self.Data[ destSlices ];
                data[mask] = convertedSourceData[mask]

                #self.Data[ destSlices ][mask] = chunk.Data[slices][mask]



    def adjustCopyParameters(self, sourceLevel, sourceBox, destinationPoint):

        # if the destination box is outside the level, it and the source corners are moved inward to fit.
        # ValueError is raised if the source corners are outside sourceLevel
        (x, y, z) = map(int, destinationPoint)

        sourceBox = BoundingBox(sourceBox.origin, sourceBox.size)

        (lx, ly, lz) = sourceBox.size;
        debug(u"Asked to copy {0} blocks \n\tfrom {1} in {3}\n\tto {2} in {4}" .format (ly * lz * lx, sourceBox, destinationPoint, sourceLevel, self))


        #clip the source ranges to this level's edges.  move the destination point as needed.
        #xxx abstract this
        if y < 0:
            sourceBox.origin[1] -= y
            sourceBox.size[1] += y
            y = 0;
        if y + sourceBox.size[1] > self.Height:
            sourceBox.size[1] -= y + sourceBox.size[1] - self.Height
            y = self.Height - sourceBox.size[1]

        #for infinite levels, don't clip along those dimensions because the 
        #infinite copy func will just skip missing chunks
        if self.Width != 0:
            if x < 0:
                sourceBox.origin[0] -= x
                sourceBox.size[0] += x
                x = 0;
            if x + sourceBox.size[0] > self.Width:
                sourceBox.size[0] -= x + sourceBox.size[0] - self.Width
                #x=self.Width-sourceBox.size[0]

        if self.Length != 0:
            if z < 0:
                sourceBox.origin[2] -= z
                sourceBox.size[2] += z
                z = 0;
            if z + sourceBox.size[2] > self.Length:
                sourceBox.size[2] -= z + sourceBox.size[2] - self.Length
                #z=self.Length-sourceBox.size[2]

        destinationPoint = (x, y, z)

        return sourceBox, destinationPoint


    def copyBlocksFrom(self, sourceLevel, sourceBox, destinationPoint, blocksToCopy=None, entities=True):
        return exhaust(self.copyBlocksFromIter(sourceLevel, sourceBox, destinationPoint, blocksToCopy, entities))

    def copyBlocksFromIter(self, sourceLevel, sourceBox, destinationPoint, blocksToCopy=None, entities=True):
        if (not sourceLevel.isInfinite) and not(
               sourceLevel.containsPoint(*sourceBox.origin) and
               sourceLevel.containsPoint(*map(lambda x:x - 1, sourceBox.maximum))):
            raise ValueError, "{0} cannot provide blocks between {1}".format(sourceLevel, sourceBox)


        sourceBox, destinationPoint = self.adjustCopyParameters(sourceLevel, sourceBox, destinationPoint)

        if min(sourceBox.size) <= 0:
            print "Empty source box, aborting"
            return;

        info(u"Copying {0} blocks from {1} to {2}" .format (sourceBox.volume, sourceBox, destinationPoint))

        if not (sourceLevel.isInfinite):
            self.copyBlocksFromFiniteToFinite(sourceLevel, sourceBox, destinationPoint, blocksToCopy)
        else:
            self.copyBlocksFromInfinite(sourceLevel, sourceBox, destinationPoint, blocksToCopy)

        for i in self.copyEntitiesFromIter(sourceLevel, sourceBox, destinationPoint, entities):
            yield i

    # --- Wool Color Conversion ---
    classicWoolMask = zeros((256,), dtype='bool')
    classicWoolMask[range(21, 37)] = True;

    classicToAlphaWoolTypes = range(21) + [
        0xE, #"Red", (21)
        0x1, #"Orange",
        0x4, #"Yellow",
        0x5, #"Light Green",
        0xD, #"Green",
        0x9, #"Aqua",
        0x3, #"Cyan",
        0xB, #"Blue",
        0xA, #"Purple",
        0xA, #"Indigo",
        0x2, #"Violet",
        0x2, #"Magenta",
        0x6, #"Pink",
        0x7, #"Black",
        0x8, #"Gray",
        0x0, #"White",
    ]
    classicToAlphaWoolTypes = array(classicToAlphaWoolTypes, dtype='uint8')

    def convertBlocksFromLevel(self, sourceLevel, blocks, blockData):
        convertedBlocks = sourceLevel.materials.conversionTables[self.materials][blocks]
        if blockData is None:
            blockData = zeros_like(convertedBlocks)

        convertedBlockData = array(blockData)

        if sourceLevel.materials is classicMaterials and self.materials is alphaMaterials:
            woolMask = self.classicWoolMask[blocks]
            woolBlocks = blocks[woolMask]
            convertedBlockData[woolMask] = self.classicToAlphaWoolTypes[woolBlocks]

        return convertedBlocks, convertedBlockData


    def saveInPlace(self):
        self.saveToFile(self.filename);

    # --- Player Methods ---
    def setPlayerPosition(self, pos, player="Player"):
        pass;

    def getPlayerPosition(self, player="Player"):
        return (8, self.Height * 0.75, 8);

    def getPlayerDimension(self, player="Player"): return 0;
    def setPlayerDimension(self, d, player="Player"): return;

    def setPlayerSpawnPosition(self, pos, player=None):
        pass;

    def playerSpawnPosition(self, player=None):
        return self.getPlayerPosition();

    def setPlayerOrientation(self, yp, player="Player"):
        pass

    def playerOrientation(self, player="Player"):
        return (-45., 0.)


    # --- Dummy Lighting Methods ---
    def generateLights(self, dirtyChunks=None):
        pass;
    def generateLightsIter(self, dirtyChunks=None):
        yield 0





class EntityLevel(MCLevel):
    """Abstract subclass of MCLevel that adds default entity behavior"""
    def copyEntitiesFromInfiniteIter(self, sourceLevel, sourceBox, destinationPoint, entities):
        chunkCount = sourceBox.chunkCount
        i = 0
        copyOffset = map(lambda x, y:x - y, destinationPoint, sourceBox.origin)
        e = t = 0

        for (chunk, slices, point) in sourceLevel.getChunkSlices(sourceBox):
            yield (i, chunkCount)
            i += 1

            if entities:
                e += len(chunk.Entities)
                for entityTag in chunk.Entities:
                    x, y, z = Entity.pos(entityTag)
                    if (x, y, z) not in sourceBox: continue

                    eTag = Entity.copyWithOffset(entityTag, copyOffset)

                    self.addEntity(eTag);

            t += len(chunk.TileEntities)
            for tileEntityTag in chunk.TileEntities:
                x, y, z = TileEntity.pos(tileEntityTag)
                if (x, y, z) not in sourceBox: continue

                eTag = TileEntity.copyWithOffset(tileEntityTag, copyOffset)

                self.addTileEntity(eTag)

        info("Copied {0} entities, {1} tile entities".format(e, t))



    def copyEntitiesFromIter(self, sourceLevel, sourceBox, destinationPoint, entities=True):
        #assume coords have already been adjusted by copyBlocks
        #if not self.hasEntities or not sourceLevel.hasEntities: return;
        sourcePoint0 = sourceBox.origin;
        sourcePoint1 = sourceBox.maximum;

        if sourceLevel.isInfinite:
            for i in self.copyEntitiesFromInfiniteIter(sourceLevel, sourceBox, destinationPoint, entities):
                yield i
        else:
            entsCopied = 0;
            tileEntsCopied = 0;
            copyOffset = map(lambda x, y:x - y, destinationPoint, sourcePoint0)
            if entities:
                for entity in sourceLevel.getEntitiesInBox(sourceBox):
                    eTag = Entity.copyWithOffset(entity, copyOffset)

                    self.addEntity(eTag)
                    entsCopied += 1;

            i = 0
            for entity in sourceLevel.getTileEntitiesInBox(sourceBox):
                i += 1
                if i % 100 == 0:
                    yield

                if not 'x' in entity: continue
                eTag = TileEntity.copyWithOffset(entity, copyOffset)

                try:
                    self.addTileEntity(eTag)
                    tileEntsCopied += 1;
                except ChunkNotPresent:
                    pass

            yield
            info(u"Copied {0} entities, {1} tile entities".format(entsCopied, tileEntsCopied))

    def getEntitiesInBox(self, box):
        """Returns a list of references to entities in this chunk, whose positions are within box"""
        return [ent for ent in self.Entities if Entity.pos(ent) in box]

    def getTileEntitiesInBox(self, box):
        """Returns a list of references to tile entities in this chunk, whose positions are within box"""
        return [ent for ent in self.TileEntities if TileEntity.pos(ent) in box]

    def removeEntitiesInBox(self, box):

        newEnts = [];
        for ent in self.Entities:
            if Entity.pos(ent) in box:
                continue;
            newEnts.append(ent);

        entsRemoved = len(self.Entities) - len(newEnts);
        debug("Removed {0} entities".format(entsRemoved))

        self.Entities.value[:] = newEnts

        return entsRemoved

    def removeTileEntitiesInBox(self, box):

        if not hasattr(self, "TileEntities"): return;
        newEnts = [];
        for ent in self.TileEntities:
            if TileEntity.pos(ent) in box:
                continue;
            newEnts.append(ent);

        entsRemoved = len(self.TileEntities) - len(newEnts);
        debug("Removed {0} tile entities".format(entsRemoved))

        self.TileEntities.value[:] = newEnts

        return entsRemoved

    def addEntities(self, entities):
        for e in entities:
            self.addEntity(e)

    def addEntity(self, entityTag):
        assert isinstance(entityTag, TAG_Compound)
        self.Entities.append(entityTag);
        self._fakeEntities = None

    def tileEntityAt(self, x, y, z):
        entities = [];
        for entityTag in self.TileEntities:
            if TileEntity.pos(entityTag) == [x, y, z]:
                entities.append(entityTag);

        if len(entities) > 1:
            info("Multiple tile entities found: {0}".format(entities))
        if len(entities) == 0:
            return None

        return entities[0];

    def addTileEntity(self, tileEntityTag):
        assert isinstance(tileEntityTag, TAG_Compound)
        def differentPosition(a):

            return not ((tileEntityTag is a) or TileEntity.pos(a) == TileEntity.pos(tileEntityTag))

        self.TileEntities.value[:] = filter(differentPosition, self.TileEntities);

        self.TileEntities.append(tileEntityTag);
        self._fakeEntities = None

    _fakeEntities = None
    def _getFakeChunkEntities(self, cx, cz):
        """distribute entities into sublists based on fake chunk position
        _fakeEntities keys are (cx,cz) and values are (Entities, TileEntities)"""
        if self._fakeEntities is None:
            self._fakeEntities = defaultdict(lambda: ([], []))
            for i, e in enumerate((self.Entities, self.TileEntities)):
                for ent in e:
                    x, y, z = [Entity, TileEntity][i].pos(ent)
                    ecx, ecz = map(lambda x:(int(floor(x)) >> 4), (x, z))

                    self._fakeEntities[ecx, ecz][i].append(ent)

        return self._fakeEntities[cx, cz]

