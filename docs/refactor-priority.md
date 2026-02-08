# Refactor Priority Board (void-client)

Generated: 2026-02-07

Deterministic rename priority for `client/refactor`, ordered by impact + remaining obfuscation.

## Scoring

- `score = fan_in*10 + fan_out*4 + mut_statics*25 + obf_hits*1.5 + size_bonus`
- Module ordering is applied first; score orders files within each module.
- `obf_hits` counts remaining identifiers matching obfuscated patterns (`anInt###`, `aClass###`, `method###`, etc.).
- Use this order for `fields -> methods -> params -> locals` per file.

## Regenerate

- `python tools/refactor/planning/refactor_priority.py --root client/refactor --write docs/refactor-priority.md --top 140 --queue 50`

## Ordered Queue

rank | module | score | fan_in | fan_out | mut_statics | obf_hits | bytes | lines | class | path
---:|---|---:|---:|---:|---:|---:|---:|---:|---|---
1 | `client/refactor/collections/` | 4647.1 | 50 | 276 | 0 | 1990 | 116125 | 1758 | `HardReferenceNode` | `client/refactor/collections/reference/HardReferenceNode.java`
2 | `client/refactor/collections/` | 1484.7 | 107 | 12 | 0 | 242 | 7442 | 160 | `Node` | `client/refactor/collections/Node.java`
3 | `client/refactor/collections/` | 1135.1 | 26 | 80 | 0 | 364 | 18167 | 282 | `SecondaryLinkedList` | `client/refactor/collections/list/SecondaryLinkedList.java`
4 | `client/refactor/collections/` | 1132.9 | 73 | 15 | 0 | 225 | 10869 | 258 | `NodeDeque` | `client/refactor/collections/deque/NodeDeque.java`
5 | `client/refactor/collections/` | 1118.5 | 76 | 11 | 0 | 208 | 5089 | 117 | `IntHashTable` | `client/refactor/collections/hash/IntHashTable.java`
6 | `client/refactor/collections/` | 835.0 | 28 | 53 | 0 | 226 | 7961 | 164 | `ByteArraySecondaryNode` | `client/refactor/collections/value/ByteArraySecondaryNode.java`
7 | `client/refactor/collections/` | 765.1 | 49 | 7 | 1 | 146 | 6192 | 169 | `HashTable` | `client/refactor/collections/hash/HashTable.java`
8 | `client/refactor/collections/` | 480.6 | 13 | 27 | 1 | 143 | 6164 | 74 | `DoublyLinkedNodeUtil` | `client/refactor/collections/DoublyLinkedNodeUtil.java`
9 | `client/refactor/collections/` | 467.2 | 37 | 14 | 0 | 27 | 1340 | 42 | `IntNode` | `client/refactor/collections/value/IntNode.java`
10 | `client/refactor/collections/` | 429.8 | 18 | 32 | 0 | 80 | 3692 | 89 | `ReferenceNode` | `client/refactor/collections/reference/ReferenceNode.java`
11 | `client/refactor/collections/` | 412.3 | 35 | 6 | 0 | 25 | 1652 | 57 | `SecondaryNode` | `client/refactor/collections/SecondaryNode.java`
12 | `client/refactor/collections/` | 411.9 | 21 | 12 | 0 | 101 | 4752 | 128 | `LinkDeque` | `client/refactor/collections/deque/LinkDeque.java`
13 | `client/refactor/collections/` | 411.2 | 18 | 27 | 0 | 81 | 3438 | 105 | `DequeIterator` | `client/refactor/collections/deque/DequeIterator.java`
14 | `client/refactor/collections/` | 373.5 | 24 | 13 | 0 | 53 | 3963 | 100 | `SecondaryLinkedListIterator` | `client/refactor/collections/list/SecondaryLinkedListIterator.java`
15 | `client/refactor/collections/` | 348.3 | 24 | 5 | 0 | 58 | 2512 | 58 | `ReferenceNodeFactory` | `client/refactor/collections/reference/ReferenceNodeFactory.java`
16 | `client/refactor/collections/` | 344.6 | 22 | 9 | 0 | 58 | 3102 | 78 | `SecondaryLinkedNodeUtil` | `client/refactor/collections/list/SecondaryLinkedNodeUtil.java`
17 | `client/refactor/collections/` | 339.6 | 12 | 15 | 0 | 105 | 4212 | 78 | `ByteArrayPool` | `client/refactor/collections/pool/ByteArrayPool.java`
18 | `client/refactor/collections/` | 333.2 | 28 | 3 | 0 | 27 | 1308 | 46 | `DoublyLinkedNode` | `client/refactor/collections/DoublyLinkedNode.java`
19 | `client/refactor/collections/` | 264.0 | 20 | 8 | 0 | 21 | 1082 | 40 | `StringNode` | `client/refactor/collections/value/StringNode.java`
20 | `client/refactor/collections/` | 181.5 | 8 | 6 | 0 | 51 | 1975 | 57 | `HashTableIterator` | `client/refactor/collections/hash/HashTableIterator.java`
21 | `client/refactor/collections/` | 132.6 | 9 | 6 | 0 | 12 | 1153 | 37 | `ShortNode` | `client/refactor/collections/value/ShortNode.java`
22 | `client/refactor/collections/` | 104.2 | 6 | 2 | 0 | 23 | 3443 | 76 | `LinkedNodeHashTable` | `client/refactor/collections/hash/LinkedNodeHashTable.java`
23 | `client/refactor/collections/` | 80.3 | 7 | 1 | 0 | 4 | 696 | 24 | `DualNode` | `client/refactor/collections/DualNode.java`
24 | `client/refactor/collections/` | 26.7 | 1 | 3 | 0 | 3 | 378 | 10 | `SoftReferenceNodeFactory` | `client/refactor/collections/reference/SoftReferenceNodeFactory.java`
25 | `client/refactor/collections/` | 18.8 | 1 | 1 | 0 | 3 | 590 | 24 | `SoftReferenceNode` | `client/refactor/collections/reference/SoftReferenceNode.java`
26 | `client/refactor/collections/` | 18.1 | 1 | 2 | 0 | 0 | 178 | 7 | `DeferredInterface19Node` | `client/refactor/collections/DeferredInterface19Node.java`
27 | `client/refactor/cache/` | 2383.1 | 138 | 16 | 0 | 619 | 21272 | 534 | `Js5Archive` | `client/refactor/cache/js5/Js5Archive.java`
28 | `client/refactor/cache/` | 1701.9 | 11 | 137 | 0 | 683 | 38849 | 552 | `ColorImageCacheSlot` | `client/refactor/cache/ColorImageCacheSlot.java`
29 | `client/refactor/cache/` | 950.9 | 61 | 12 | 0 | 192 | 9794 | 238 | `SoftLruCache` | `client/refactor/cache/SoftLruCache.java`
30 | `client/refactor/cache/` | 878.6 | 10 | 26 | 0 | 443 | 20196 | 433 | `Js5ArchiveProviderImpl` | `client/refactor/cache/js5/Js5ArchiveProviderImpl.java`
31 | `client/refactor/cache/` | 768.2 | 24 | 8 | 0 | 325 | 17322 | 374 | `Js5NetQueue` | `client/refactor/cache/js5/Js5NetQueue.java`
32 | `client/refactor/cache/` | 561.2 | 20 | 7 | 0 | 220 | 6369 | 142 | `Js5Index` | `client/refactor/cache/js5/Js5Index.java`
33 | `client/refactor/cache/` | 560.9 | 17 | 27 | 0 | 186 | 7874 | 166 | `ColorImageCache` | `client/refactor/cache/ColorImageCache.java`
34 | `client/refactor/cache/` | 502.5 | 18 | 14 | 0 | 174 | 11087 | 259 | `ReferenceCache` | `client/refactor/cache/ReferenceCache.java`
35 | `client/refactor/cache/` | 459.3 | 13 | 9 | 0 | 193 | 7568 | 151 | `ResourceSlot` | `client/refactor/cache/loading/ResourceSlot.java`
36 | `client/refactor/cache/` | 444.7 | 11 | 21 | 0 | 165 | 6328 | 135 | `IntImageCache` | `client/refactor/cache/IntImageCache.java`
37 | `client/refactor/cache/` | 436.4 | 13 | 20 | 0 | 149 | 5782 | 147 | `ResourceDownloadTask` | `client/refactor/cache/loading/ResourceDownloadTask.java`
38 | `client/refactor/cache/` | 381.1 | 10 | 39 | 0 | 82 | 4141 | 62 | `CacheIndexCrcUtil` | `client/refactor/cache/CacheIndexCrcUtil.java`
39 | `client/refactor/cache/` | 331.4 | 20 | 10 | 0 | 60 | 2749 | 59 | `GroundItemPileCache` | `client/refactor/cache/GroundItemPileCache.java`
40 | `client/refactor/cache/` | 318.0 | 24 | 5 | 0 | 38 | 1974 | 49 | `Js5ArchiveProvider` | `client/refactor/cache/js5/Js5ArchiveProvider.java`
41 | `client/refactor/cache/` | 310.3 | 16 | 14 | 0 | 62 | 2527 | 53 | `Js5ResourceProviders` | `client/refactor/cache/js5/Js5ResourceProviders.java`
42 | `client/refactor/cache/` | 302.3 | 20 | 7 | 0 | 48 | 4610 | 113 | `MonochromeImageCacheSlot` | `client/refactor/cache/MonochromeImageCacheSlot.java`
43 | `client/refactor/cache/` | 282.7 | 19 | 11 | 0 | 32 | 1409 | 39 | `ClientCaches` | `client/refactor/cache/ClientCaches.java`
44 | `client/refactor/cache/` | 250.6 | 9 | 9 | 0 | 81 | 6165 | 134 | `Js5MasterIndex` | `client/refactor/cache/js5/Js5MasterIndex.java`
45 | `client/refactor/cache/` | 235.3 | 11 | 5 | 0 | 69 | 3615 | 100 | `LruHashTable` | `client/refactor/cache/LruHashTable.java`
46 | `client/refactor/cache/` | 210.6 | 12 | 7 | 0 | 41 | 2246 | 60 | `HardReferenceCacheEntry` | `client/refactor/cache/HardReferenceCacheEntry.java`
47 | `client/refactor/cache/` | 192.2 | 2 | 0 | 4 | 47 | 3480 | 82 | `FileCache` | `client/refactor/cache/FileCache.java`
48 | `client/refactor/cache/` | 191.9 | 16 | 3 | 0 | 13 | 725 | 22 | `ReferenceCacheEntryConverter` | `client/refactor/cache/ReferenceCacheEntryConverter.java`
49 | `client/refactor/cache/` | 189.9 | 14 | 6 | 0 | 17 | 880 | 26 | `ConfigArchive` | `client/refactor/cache/js5/ConfigArchive.java`
50 | `client/refactor/cache/` | 140.9 | 11 | 2 | 0 | 15 | 797 | 30 | `ReferenceCacheEntry` | `client/refactor/cache/ReferenceCacheEntry.java`
51 | `client/refactor/cache/` | 53.1 | 5 | 0 | 0 | 2 | 182 | 9 | `CacheKey` | `client/refactor/cache/CacheKey.java`
52 | `client/refactor/cache/` | 26.7 | 1 | 3 | 0 | 3 | 460 | 10 | `SoftReferenceCacheEntryConverter` | `client/refactor/cache/SoftReferenceCacheEntryConverter.java`
53 | `client/refactor/cache/` | 22.8 | 1 | 2 | 0 | 3 | 661 | 24 | `SoftReferenceCacheEntry` | `client/refactor/cache/SoftReferenceCacheEntry.java`
54 | `client/refactor/io/` | 3191.0 | 209 | 49 | 0 | 594 | 28026 | 760 | `JagBuffer` | `client/refactor/io/buffer/JagBuffer.java`
55 | `client/refactor/io/` | 1129.4 | 2 | 2 | 1 | 711 | 19857 | 446 | `BZip2Decompressor` | `client/refactor/io/compress/BZip2Decompressor.java`
56 | `client/refactor/io/` | 558.4 | 11 | 29 | 0 | 218 | 10847 | 199 | `ArchiveDiskActionHandler` | `client/refactor/io/ArchiveDiskActionHandler.java`
57 | `client/refactor/io/` | 490.8 | 12 | 4 | 0 | 233 | 10554 | 249 | `BufferedFile` | `client/refactor/io/BufferedFile.java`
58 | `client/refactor/io/` | 480.8 | 10 | 3 | 0 | 243 | 8507 | 160 | `ArchiveDisk` | `client/refactor/io/ArchiveDisk.java`
59 | `client/refactor/io/` | 349.8 | 23 | 8 | 0 | 57 | 4555 | 95 | `GzipDecompressor` | `client/refactor/io/compress/GzipDecompressor.java`
60 | `client/refactor/io/` | 335.9 | 9 | 28 | 0 | 88 | 3828 | 110 | `BZip2State` | `client/refactor/io/compress/BZip2State.java`
61 | `client/refactor/io/` | 230.3 | 15 | 2 | 0 | 47 | 3651 | 113 | `FileOnDisk` | `client/refactor/io/FileOnDisk.java`
62 | `client/refactor/io/` | 210.4 | 13 | 6 | 0 | 37 | 1838 | 58 | `ArchiveDiskAction` | `client/refactor/io/ArchiveDiskAction.java`
63 | `client/refactor/io/` | 188.4 | 16 | 1 | 0 | 16 | 742 | 25 | `AbstractByteArrayCopier` | `client/refactor/io/memory/AbstractByteArrayCopier.java`
64 | `client/refactor/io/` | 113.6 | 8 | 3 | 0 | 14 | 1186 | 33 | `RandomDatFile` | `client/refactor/io/RandomDatFile.java`
65 | `client/refactor/io/` | 29.5 | 1 | 1 | 0 | 10 | 921 | 32 | `DirectByteArrayCopier` | `client/refactor/io/memory/DirectByteArrayCopier.java`
66 | `client/refactor/net/` | 1250.8 | 12 | 108 | 0 | 455 | 32534 | 483 | `LoginHandshake` | `client/refactor/net/LoginHandshake.java`
67 | `client/refactor/net/` | 1249.4 | 9 | 57 | 0 | 613 | 23874 | 285 | `ZoneUpdateDecoder` | `client/refactor/net/packets/ZoneUpdateDecoder.java`
68 | `client/refactor/net/` | 1152.9 | 112 | 1 | 0 | 19 | 833 | 34 | `ServerPacket` | `client/refactor/net/packets/ServerPacket.java`
69 | `client/refactor/net/` | 943.8 | 30 | 43 | 0 | 310 | 13510 | 217 | `PacketWriter` | `client/refactor/net/packets/PacketWriter.java`
70 | `client/refactor/net/` | 928.7 | 87 | 4 | 0 | 28 | 1344 | 49 | `ClientPacket` | `client/refactor/net/packets/ClientPacket.java`
71 | `client/refactor/net/` | 686.0 | 36 | 4 | 0 | 205 | 4944 | 122 | `InboundPacketBuffer` | `client/refactor/net/packets/InboundPacketBuffer.java`
72 | `client/refactor/net/` | 659.8 | 46 | 27 | 0 | 60 | 3679 | 76 | `PacketBufferNode` | `client/refactor/net/packets/PacketBufferNode.java`
73 | `client/refactor/net/` | 536.0 | 26 | 11 | 0 | 153 | 5017 | 145 | `PacketBuffer` | `client/refactor/net/packets/PacketBuffer.java`
74 | `client/refactor/net/` | 534.9 | 35 | 23 | 0 | 61 | 2785 | 67 | `ClientConnectionKeepAlive` | `client/refactor/net/ClientConnectionKeepAlive.java`
75 | `client/refactor/net/` | 490.6 | 18 | 36 | 0 | 109 | 6227 | 116 | `ServerEndpoint` | `client/refactor/net/ServerEndpoint.java`
76 | `client/refactor/net/` | 486.9 | 8 | 17 | 0 | 222 | 11809 | 193 | `SocketConnection` | `client/refactor/net/SocketConnection.java`
77 | `client/refactor/net/` | 434.6 | 15 | 14 | 0 | 150 | 7217 | 158 | `WorldListEntryBase` | `client/refactor/net/worldlist/WorldListEntryBase.java`
78 | `client/refactor/net/` | 431.3 | 20 | 13 | 0 | 118 | 4624 | 93 | `WorldListEntry` | `client/refactor/net/worldlist/WorldListEntry.java`
79 | `client/refactor/net/` | 418.9 | 18 | 19 | 0 | 106 | 7824 | 223 | `BufferedSocket` | `client/refactor/net/BufferedSocket.java`
80 | `client/refactor/net/` | 357.7 | 11 | 22 | 0 | 105 | 4304 | 87 | `WorldListDecoder` | `client/refactor/net/worldlist/WorldListDecoder.java`
81 | `client/refactor/net/` | 339.9 | 14 | 23 | 0 | 71 | 2752 | 70 | `SocketFactory` | `client/refactor/net/SocketFactory.java`
82 | `client/refactor/net/` | 329.3 | 31 | 1 | 0 | 10 | 617 | 25 | `ZoneUpdateType` | `client/refactor/net/packets/ZoneUpdateType.java`
83 | `client/refactor/net/` | 328.8 | 10 | 21 | 0 | 95 | 4526 | 75 | `PacketBufferNodeFactory` | `client/refactor/net/packets/PacketBufferNodeFactory.java`
84 | `client/refactor/net/` | 312.3 | 19 | 11 | 0 | 51 | 3536 | 104 | `PingRequester` | `client/refactor/net/PingRequester.java`
85 | `client/refactor/net/` | 269.3 | 14 | 5 | 0 | 72 | 2673 | 74 | `ProxyAuthenticationException` | `client/refactor/net/ProxyAuthenticationException.java`
86 | `client/refactor/net/` | 260.7 | 14 | 13 | 0 | 45 | 2322 | 52 | `ClientStringPacketSender` | `client/refactor/net/packets/ClientStringPacketSender.java`
87 | `client/refactor/net/` | 182.0 | 13 | 5 | 0 | 21 | 1095 | 37 | `PingRequest` | `client/refactor/net/PingRequest.java`
88 | `client/refactor/net/` | 147.6 | 9 | 3 | 0 | 30 | 1108 | 38 | `Connection` | `client/refactor/net/Connection.java`
89 | `client/refactor/net/` | 129.9 | 10 | 4 | 0 | 9 | 761 | 25 | `PacketBufferNodePool` | `client/refactor/net/packets/PacketBufferNodePool.java`
90 | `client/refactor/net/` | 106.2 | 9 | 1 | 0 | 8 | 472 | 18 | `PacketConstants` | `client/refactor/net/packets/PacketConstants.java`
91 | `client/refactor/net/` | 76.7 | 1 | 2 | 0 | 37 | 6382 | 132 | `ProxySocketFactory` | `client/refactor/net/ProxySocketFactory.java`
92 | `client/refactor/net/` | 17.2 | 1 | 1 | 0 | 2 | 394 | 17 | `DirectSocketFactory` | `client/refactor/net/DirectSocketFactory.java`
93 | `client/refactor/config/` | 2095.6 | 44 | 40 | 0 | 982 | 45150 | 743 | `NpcType` | `client/refactor/config/NpcType.java`
94 | `client/refactor/config/` | 1793.7 | 48 | 23 | 0 | 801 | 40496 | 726 | `ObjectType` | `client/refactor/config/ObjectType.java`
95 | `client/refactor/config/` | 1746.1 | 19 | 24 | 0 | 960 | 40151 | 741 | `ItemType` | `client/refactor/config/ItemType.java`
96 | `client/refactor/config/` | 1512.6 | 67 | 18 | 0 | 508 | 17227 | 337 | `SeqType` | `client/refactor/config/SeqType.java`
97 | `client/refactor/config/` | 951.5 | 18 | 65 | 0 | 335 | 17908 | 317 | `ItemTypeList` | `client/refactor/config/ItemTypeList.java`
98 | `client/refactor/config/` | 919.9 | 13 | 57 | 0 | 369 | 16794 | 288 | `BASTypeList` | `client/refactor/config/BASTypeList.java`
99 | `client/refactor/config/` | 900.3 | 36 | 5 | 0 | 342 | 14595 | 287 | `BASType` | `client/refactor/config/BASType.java`
100 | `client/refactor/config/` | 894.1 | 9 | 19 | 1 | 463 | 17169 | 365 | `ObjectTypeList` | `client/refactor/config/ObjectTypeList.java`
101 | `client/refactor/config/` | 717.8 | 18 | 33 | 0 | 267 | 10699 | 202 | `QuickChatCatType` | `client/refactor/config/QuickChatCatType.java`
102 | `client/refactor/config/` | 704.6 | 21 | 16 | 0 | 284 | 9176 | 227 | `IDKType` | `client/refactor/config/IDKType.java`
103 | `client/refactor/config/` | 703.5 | 17 | 18 | 0 | 303 | 13943 | 258 | `HitmarkTypeList` | `client/refactor/config/HitmarkTypeList.java`
104 | `client/refactor/config/` | 684.4 | 18 | 14 | 0 | 295 | 11761 | 212 | `GfxType` | `client/refactor/config/GfxType.java`
105 | `client/refactor/config/` | 679.5 | 14 | 2 | 0 | 350 | 13022 | 281 | `ParticleEmitterType` | `client/refactor/config/ParticleEmitterType.java`
106 | `client/refactor/config/` | 575.9 | 18 | 11 | 0 | 231 | 10815 | 244 | `MapElementType` | `client/refactor/config/MapElementType.java`
107 | `client/refactor/config/` | 532.7 | 17 | 11 | 0 | 210 | 7430 | 184 | `HitmarkType` | `client/refactor/config/HitmarkType.java`
108 | `client/refactor/config/` | 531.2 | 26 | 8 | 0 | 157 | 7391 | 146 | `QuestType` | `client/refactor/config/QuestType.java`
109 | `client/refactor/config/` | 497.5 | 18 | 20 | 0 | 156 | 6902 | 145 | `FloorUnderlayType` | `client/refactor/config/FloorUnderlayType.java`
110 | `client/refactor/config/` | 496.7 | 17 | 21 | 0 | 160 | 5340 | 112 | `MapSceneType` | `client/refactor/config/MapSceneType.java`
111 | `client/refactor/config/` | 470.5 | 16 | 22 | 0 | 146 | 7024 | 177 | `QuickChatPhraseType` | `client/refactor/config/QuickChatPhraseType.java`
112 | `client/refactor/config/` | 418.3 | 25 | 15 | 0 | 71 | 3515 | 93 | `EnumTypeList` | `client/refactor/config/EnumTypeList.java`
113 | `client/refactor/config/` | 410.7 | 23 | 12 | 0 | 87 | 4376 | 107 | `IDKTypeList` | `client/refactor/config/IDKTypeList.java`
114 | `client/refactor/config/` | 351.2 | 16 | 18 | 0 | 78 | 4312 | 91 | `EnumStringLookupEntry` | `client/refactor/config/EnumStringLookupEntry.java`
115 | `client/refactor/config/` | 332.3 | 18 | 13 | 0 | 65 | 5528 | 134 | `FloorUnderlayTypeList` | `client/refactor/config/FloorUnderlayTypeList.java`
116 | `client/refactor/config/` | 330.9 | 29 | 3 | 0 | 19 | 832 | 34 | `ScriptVarType` | `client/refactor/config/ScriptVarType.java`
117 | `client/refactor/config/` | 329.2 | 18 | 11 | 0 | 69 | 3344 | 102 | `QuestTypeList` | `client/refactor/config/QuestTypeList.java`
118 | `client/refactor/config/` | 322.3 | 11 | 13 | 0 | 105 | 5505 | 132 | `EnumType` | `client/refactor/config/EnumType.java`
119 | `client/refactor/config/` | 292.7 | 15 | 9 | 0 | 70 | 3424 | 101 | `GfxTypeList` | `client/refactor/config/GfxTypeList.java`
120 | `client/refactor/config/` | 267.9 | 10 | 10 | 0 | 84 | 3807 | 109 | `SeqTypeList` | `client/refactor/config/SeqTypeList.java`
121 | `client/refactor/config/` | 242.9 | 6 | 12 | 0 | 89 | 2741 | 64 | `VarPlayerType` | `client/refactor/config/VarPlayerType.java`
122 | `client/refactor/config/` | 189.8 | 12 | 3 | 0 | 38 | 1673 | 57 | `ParamType` | `client/refactor/config/ParamType.java`
123 | `client/refactor/config/` | 183.7 | 13 | 2 | 0 | 30 | 1406 | 52 | `VarBitType` | `client/refactor/config/VarBitType.java`
124 | `client/refactor/config/` | 175.8 | 12 | 2 | 0 | 31 | 2619 | 54 | `VarcType` | `client/refactor/config/VarcType.java`
125 | `client/refactor/config/` | 141.9 | 7 | 8 | 0 | 26 | 1723 | 48 | `InvTypeList` | `client/refactor/config/InvTypeList.java`
126 | `client/refactor/config/` | 95.4 | 6 | 2 | 0 | 18 | 826 | 31 | `InvType` | `client/refactor/config/InvType.java`
127 | `client/refactor/config/` | 10.1 | 1 | 0 | 0 | 0 | 117 | 6 | `ClientScriptTriggerType` | `client/refactor/config/ClientScriptTriggerType.java`
128 | `client/refactor/game/` | 2423.9 | 53 | 66 | 3 | 1024 | 37706 | 815 | `Actor` | `client/refactor/game/entity/Actor.java`
129 | `client/refactor/game/` | 2256.0 | 44 | 65 | 0 | 1025 | 37096 | 667 | `Player` | `client/refactor/game/entity/Player.java`
130 | `client/refactor/game/` | 2100.0 | 40 | 71 | 0 | 930 | 42070 | 541 | `ModeGame` | `client/refactor/game/mode/ModeGame.java`
131 | `client/refactor/game/` | 1805.0 | 25 | 61 | 0 | 864 | 30077 | 417 | `ParticleEmitterInstance` | `client/refactor/game/particle/ParticleEmitterInstance.java`
132 | `client/refactor/game/` | 1647.5 | 42 | 32 | 0 | 725 | 24021 | 437 | `Npc` | `client/refactor/game/entity/Npc.java`
133 | `client/refactor/game/` | 1393.0 | 15 | 112 | 0 | 515 | 44946 | 707 | `VarClientStringList` | `client/refactor/game/vars/VarClientStringList.java`
134 | `client/refactor/game/` | 1084.5 | 19 | 60 | 0 | 431 | 16028 | 337 | `GraphicsObject` | `client/refactor/game/entity/GraphicsObject.java`
135 | `client/refactor/game/` | 1027.9 | 93 | 13 | 0 | 30 | 1753 | 47 | `LoginManager` | `client/refactor/game/login/LoginManager.java`
136 | `client/refactor/game/` | 989.8 | 11 | 49 | 0 | 450 | 17598 | 326 | `VarDomainImpl` | `client/refactor/game/vars/VarDomainImpl.java`
137 | `client/refactor/game/` | 982.4 | 8 | 51 | 0 | 460 | 16723 | 245 | `TimedVarEntry` | `client/refactor/game/vars/TimedVarEntry.java`
138 | `client/refactor/game/` | 905.5 | 4 | 24 | 0 | 505 | 23984 | 365 | `ParticleInstance` | `client/refactor/game/particle/ParticleInstance.java`
139 | `client/refactor/game/` | 833.5 | 8 | 33 | 0 | 409 | 16046 | 343 | `Projectile` | `client/refactor/game/entity/Projectile.java`
140 | `client/refactor/game/` | 670.8 | 13 | 31 | 0 | 274 | 11568 | 280 | `StaticFloorDecorationEntity` | `client/refactor/game/entity/StaticFloorDecorationEntity.java`

## Active Queue Checklist

- [ ] `client/refactor/collections/reference/HardReferenceNode.java`
- [ ] `client/refactor/collections/Node.java`
- [ ] `client/refactor/collections/list/SecondaryLinkedList.java`
- [ ] `client/refactor/collections/deque/NodeDeque.java`
- [ ] `client/refactor/collections/hash/IntHashTable.java`
- [ ] `client/refactor/collections/value/ByteArraySecondaryNode.java`
- [ ] `client/refactor/collections/hash/HashTable.java`
- [ ] `client/refactor/collections/DoublyLinkedNodeUtil.java`
- [ ] `client/refactor/collections/value/IntNode.java`
- [ ] `client/refactor/collections/reference/ReferenceNode.java`
- [ ] `client/refactor/collections/SecondaryNode.java`
- [ ] `client/refactor/collections/deque/LinkDeque.java`
- [ ] `client/refactor/collections/deque/DequeIterator.java`
- [ ] `client/refactor/collections/list/SecondaryLinkedListIterator.java`
- [ ] `client/refactor/collections/reference/ReferenceNodeFactory.java`
- [ ] `client/refactor/collections/list/SecondaryLinkedNodeUtil.java`
- [ ] `client/refactor/collections/pool/ByteArrayPool.java`
- [ ] `client/refactor/collections/DoublyLinkedNode.java`
- [ ] `client/refactor/collections/value/StringNode.java`
- [ ] `client/refactor/collections/hash/HashTableIterator.java`
- [ ] `client/refactor/collections/value/ShortNode.java`
- [ ] `client/refactor/collections/hash/LinkedNodeHashTable.java`
- [ ] `client/refactor/collections/DualNode.java`
- [ ] `client/refactor/collections/reference/SoftReferenceNodeFactory.java`
- [ ] `client/refactor/collections/reference/SoftReferenceNode.java`
- [ ] `client/refactor/collections/DeferredInterface19Node.java`
- [ ] `client/refactor/cache/js5/Js5Archive.java`
- [ ] `client/refactor/cache/ColorImageCacheSlot.java`
- [ ] `client/refactor/cache/SoftLruCache.java`
- [ ] `client/refactor/cache/js5/Js5ArchiveProviderImpl.java`
- [ ] `client/refactor/cache/js5/Js5NetQueue.java`
- [ ] `client/refactor/cache/js5/Js5Index.java`
- [ ] `client/refactor/cache/ColorImageCache.java`
- [ ] `client/refactor/cache/ReferenceCache.java`
- [ ] `client/refactor/cache/loading/ResourceSlot.java`
- [ ] `client/refactor/cache/IntImageCache.java`
- [ ] `client/refactor/cache/loading/ResourceDownloadTask.java`
- [ ] `client/refactor/cache/CacheIndexCrcUtil.java`
- [ ] `client/refactor/cache/GroundItemPileCache.java`
- [ ] `client/refactor/cache/js5/Js5ArchiveProvider.java`
- [ ] `client/refactor/cache/js5/Js5ResourceProviders.java`
- [ ] `client/refactor/cache/MonochromeImageCacheSlot.java`
- [ ] `client/refactor/cache/ClientCaches.java`
- [ ] `client/refactor/cache/js5/Js5MasterIndex.java`
- [ ] `client/refactor/cache/LruHashTable.java`
- [ ] `client/refactor/cache/HardReferenceCacheEntry.java`
- [ ] `client/refactor/cache/FileCache.java`
- [ ] `client/refactor/cache/ReferenceCacheEntryConverter.java`
- [ ] `client/refactor/cache/js5/ConfigArchive.java`
- [ ] `client/refactor/cache/ReferenceCacheEntry.java`
