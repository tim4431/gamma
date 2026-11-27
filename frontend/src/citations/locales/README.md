# Citation locales

The terms, date formats and punctuation rules that citation styles print
in each language: every locale of the Citation Style Language, 63 files,
plus `locales.json`, which names each language's primary dialect and the
language names Settings → Citations lists. citeproc-js asks for a locale
synchronously, so [cslAssets.js](../cslAssets.js) loads the ones a style
needs (the chosen language, the style's default-locale, every `locale=` it
tests, and en-US, which citeproc always reads) before an engine is built.
Each file is a lazy chunk of its own; a language without a file of its own
is rendered with its primary dialect, else en-US.

These locales come from the [Citation Style Language](https://citationstyles.org/)
project: [citation-style-language/locales](https://github.com/citation-style-language/locales),
commit `21a175a1d84cc1792dd73f5a627de004fba5b207`, unchanged, with the
listings of translators in each file's metadata kept as they are. They are
released under the Creative Commons Attribution-ShareAlike 3.0 Unported
license ([LICENSE](LICENSE)); the project asks software that uses them to
mention the CSL project and link to [citationstyles.org](https://citationstyles.org/),
which Settings → Citations does.

| File | Bytes | SHA-256 |
|---|---|---|
| locales-af-ZA.xml | 30352 | `c20462bef7e05b51ad1f9f246b36ed0cf9686254445299ef72ad9b898280b282` |
| locales-ar.xml | 26865 | `4b9240652d3bbf9eb2d9a5fc5894810c2180c40624c55e605f3297f4d8f03215` |
| locales-bal-PK.xml | 18502 | `e7c0822879ba318f832a8b08ad7e39c2193d613b08af73848478e2c499ebe30b` |
| locales-bg-BG.xml | 27982 | `7c6dd2fdf0be4f8ada9833c090becb23ebeb58157a858bb0ea00184decb230f5` |
| locales-brh-PK.xml | 18732 | `c06b830ab39fcdcd184f5279239aefa16aa7091f0144a616e329ae013f4bb681` |
| locales-ca-AD.xml | 26856 | `9474ed02f4535544f7225d7d3c55d8d3376f8423cfe78e9ffb8cacbd109f0377` |
| locales-cs-CZ.xml | 25114 | `146e942d19c150c05cf3ad6900c7ac1f1b5c122294a6212478a604186f0d35e6` |
| locales-cy-GB.xml | 25064 | `e509a2d60c2fc32e9cdd71c2a4c295ad47aaf1ff85c8486c67c6357073bf8a5a` |
| locales-da-DK.xml | 24975 | `b89e559961e0af32dd4113c65c3034812317c8630adc6fc8803b17db691586f3` |
| locales-de-AT.xml | 25715 | `12f4934b58701c0cb4a59ec1389f7ba03a0341357a5789d7e9ee82210c522e09` |
| locales-de-CH.xml | 25510 | `200568ef48f331585e3f7d186cfd34129f9f9ba2ceeaf28105ac5b3dc550450d` |
| locales-de-DE.xml | 25679 | `befaeb7c9b4f2ce8e4b7e4ef028500c317ba4e92a5edf2125dad267b9b9951ad` |
| locales-el-GR.xml | 26733 | `f3c1220eb768a6bde9189158679443d81fa6587c182c6a65fbc483cb29f37333` |
| locales-en-GB.xml | 32644 | `e4a1fed9cec04b5fed5e9027b4055925cd4d8f7c9fe233eb0ceca6d4be3999f6` |
| locales-en-US.xml | 32649 | `ac864c7c21166b4390d82c31792cdc509400727fa0060b43d8aa17e07f9cb079` |
| locales-es-CL.xml | 24869 | `3e25e64d5f872dc139182ca71c142d97c863c0c709d58b87c8467f86b6acfe30` |
| locales-es-ES.xml | 24794 | `ae4a12f612be754e54843ba88ae6641eb708647cea164b5b922bb6bf9b21c91f` |
| locales-es-MX.xml | 26025 | `2287c429cb73361034d3d1dc03a6a5444c5e4cd322d08a3b1e06854aa4c35477` |
| locales-et-EE.xml | 24775 | `df90c1e533e6131b43c1195798a0d7583aae1e065eeaea5e68b423df082e8a44` |
| locales-eu.xml | 25350 | `b7a45bc0eba06d3be3507f26c3f52e2059b1d9c7b5bfe2990723e36c02736852` |
| locales-fa-IR.xml | 25951 | `7bef62d4f29b33a9e0bdd823222cbf04dc86a0af59e0d5acf697cc098be78cf6` |
| locales-fi-FI.xml | 25710 | `e07b114ab23125229c1a3a9f320465fb11273cce330d0cd17178378e3846bbe4` |
| locales-fr-CA.xml | 31828 | `5dfed8e5c0632a79ad95fabad22e3a84f301aab46385ac5615a91a1aab0edfbd` |
| locales-fr-FR.xml | 31110 | `bd6820660e6a4dab73bfd48df7348320708263b413c06fdd2e5445bcdf788bfb` |
| locales-gl-ES.xml | 26258 | `8907a8c07369566c3c595b165e2845cfa2bd52a51ad7ab858819d46d594cecaf` |
| locales-he-IL.xml | 25941 | `27b469638930ea3854728232dac3016fa3133b44cceae544db86d150d51d9264` |
| locales-hi-IN.xml | 28054 | `6c0bdea2834a8684a8ddad04e238adb0942e12f2ad81178cd955d4d25e93944b` |
| locales-hr-HR.xml | 24845 | `7cc37b61e0c2e6ff3c15e0733dc6891bc91db88799393451d808c38a01b72237` |
| locales-hu-HU.xml | 25415 | `e01306dce1d06963562d987347d36fb60bb57d37233cd03ee24bd36a1ef4ca15` |
| locales-hy-AM.xml | 30908 | `7abf9fb398898921aec687006beeb90afd3f2bfc7311adaac115ba3be1fc4357` |
| locales-id-ID.xml | 25245 | `47135d3f246528535ee58f0104772e1d643799a0586737c7f428d21826bee4af` |
| locales-is-IS.xml | 25193 | `fd0f6661c198b442264ccff5aa9696d7ad9d053bda6c4aa95289f907fe64b64d` |
| locales-it-IT.xml | 27219 | `71ac17266a44a9054f4203d75fcf7a14588c621d06280177902bbc922f124778` |
| locales-ja-JP.xml | 24352 | `5167d0686946405a0531c484b61a6e0e43b652631443f06908b3396ce6ffb543` |
| locales-km-KH.xml | 25514 | `f4ef777bf40094b6b35b37983c49b8e7b0ff99909553dd58f6de66887c3a0599` |
| locales-ko-KR.xml | 24902 | `15098745c3b0af03b6fc420d6373fb7b00426dfbbb2bf53005b855cd0ed11e0f` |
| locales-la.xml | 24957 | `babd9ba17e824df58d4f170f99491c5c43bb963775ba95badaec9c7b50f8cf4e` |
| locales-lij-IT.xml | 27253 | `199d9df22efa595aaa9c33575d8019f13e7e6bc10a06a8346cd09fecc57fdc7b` |
| locales-lt-LT.xml | 26195 | `3e162b21502d2951433785551d970de568e0f7b06cd3b55e8dd567a9d0b123af` |
| locales-lv-LV.xml | 27254 | `01294b42627684164999649cb376bb1a694f8d816cd1db2f693361014782543b` |
| locales-mn-MN.xml | 25485 | `60d3cebf7c4f5ec45fec7171bbb21604d12925e64ac2811a568f0c1927946bb7` |
| locales-ms-MY.xml | 11815 | `d1df2e46b1d6fa7f3510604afed360b1463807caf809976413a656f193e343aa` |
| locales-nb-NO.xml | 22093 | `148b44e4ea4dfcb9396d24c679bef734fcfd1513cedf63c694c4484f5270d39f` |
| locales-nl-NL.xml | 26431 | `6e5e7f23ed696903b05e36d766b4334b6a6863653d5a1176706df23492deb6d5` |
| locales-nn-NO.xml | 22082 | `16bca07435dc6461bfa52221063fe3a94cce51e022e2b9ae625b38c3b5812d76` |
| locales-pa-PK.xml | 21183 | `5b22373c95d7fe571e1a3df75e6f73c71298e1c2c13e1eb4944c38c4d255f8f2` |
| locales-pl-PL.xml | 25348 | `6b87b767e2b08bdd3fd18f459b0001c30fc9980c53d90dea3e3c166d6c23327f` |
| locales-pt-BR.xml | 27363 | `f67d781260405ea7617d43e20eb0c363e4e3db4e420d9b017ba9c9c2b797e12b` |
| locales-pt-PT.xml | 26377 | `319c2d60bd46355155f549b646078c345e492309b2347db5ea309d2469b689b3` |
| locales-ro-RO.xml | 25555 | `578a95c8b3d7dac6013e0fadbb31dd8d925f27f2b9ed97fbc7e718c449ceef2b` |
| locales-ru-RU.xml | 29254 | `382aaf40e674267ede7fbb5529a721a9edb5f127970f93278b2a62596c186071` |
| locales-sk-SK.xml | 25336 | `51600e20de1f57ab128af5f6a8b1a79b3a65bb1c03d39f075a73a484bdf573e0` |
| locales-sl-SI.xml | 24880 | `73e67a69a2105edac46742079ec42b5b06e4edc29a028506366da4429dc4161f` |
| locales-sr-Cyrl-RS.xml | 31429 | `86e7bcbceb265edeab12ed4aa93f999f70f494ec0df7e62d78e5c913be7dbd27` |
| locales-sr-Latn-RS.xml | 28319 | `ecf5ef0514d2d83a5d18f1306bc463306d8f0ababf9414dd8f911ba9f2a58ae1` |
| locales-sv-SE.xml | 22711 | `f0c6bdc4974f2df06ee250ffcec585f5665c230b3e7bc047b0b0ad6e328154ba` |
| locales-th-TH.xml | 27244 | `fc076aca10db217767fe1f5351e6cd5a41c58de069d691c5750fc96d08c5438b` |
| locales-tl-PH.xml | 32918 | `3aa28cf29e92ae69cfaaa433d28e8a7cb53d2955c545679d2288c355dc8e2c57` |
| locales-tr-TR.xml | 25478 | `018b01a383c0e619c5366146b339f3a79cff594738f12c3012733c0f97e9680d` |
| locales-uk-UA.xml | 26752 | `e967c8d1a13606d918c1074e0fc4d10add62f5eff56eadb42c25b3dad78e580d` |
| locales-vi-VN.xml | 26563 | `5d06474f045a78fd15a4c7e1f9a01bd63e1bbb3b982bef5f5bc9a9d931141c2e` |
| locales-zh-CN.xml | 18641 | `95cebc667f2da8fb5b9864e49941e3f867d59f79feba929bc58e8ed6bfd4d582` |
| locales-zh-TW.xml | 22273 | `207f39ff0c6f287fbffbd08d48b263d6aee8053125b7db54f4ef26899f5a7912` |
| locales.json | 6666 | `82faf814d3d86cf15132dd6062ee143090609b4f01b9fe8e422d8024ca4eabb2` |

frontend/tests/citations.test.mjs checks each file against its size and
hash here (with LF line endings, as the repository has them). To move to
newer locales, take every file from one commit and put its hash and theirs
in this table.
