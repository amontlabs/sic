# Gold set: débat Macron / Le Pen, 20 avril 2022

File: `2022-macron-lepen.jsonl`. It has one JSON object per line and covers the second-round debate on TF1/France 2, moderated by Léa Salamé and Gilles Bouleau.

## Counts

- **56 claims**: Le Pen 32, Macron 24.
- Verdicts: exact 11, plutot_exact 15, imprecis 7, trompeur 8, faux 14, inverifiable 1.

| speaker | exact | plutot_exact | imprecis | trompeur | faux | inverifiable |
|---|---|---|---|---|---|---|
| lepen  | 4 | 7 | 5 | 6 | 9 | 1 |
| macron | 7 | 8 | 2 | 2 | 5 | 0 |

## Method notes

- When several outlets checked the same claim, the entries were merged into one claim with multiple `refs`. When outlets disagreed, the most cautious verdict was kept and the disagreement is noted in `explanation`. Examples are the inflation/croissance claim, the "deux tiers" Covid debt claim, Le Pen's claim on travailleurs détachés, and fermetures de classes.
- `refs[].label` is the outlet's own label when it published one. It is empty when the article gives no explicit verdict (most Libération CheckNews pieces, JDD, BFMTV, L'Obs, Ouest-France, Le Figaro). The TF1 Info labels are the icons used in its live blog (✅ / ❌ / ⚠️).
- RTBF (Faky) is a secondary roundup of Le Monde, franceinfo and Libération. Its labels are kept for reference. Two of its verdicts reflect early versions that the original outlets later corrected:
  - franceinfo's "80 % violences intrafamiliales" item was first rated false, then corrected to true.
  - Libération's greenhouse-gas item was corrected from -6.9 % to -9.2 %.
  The gold verdicts follow the corrected versions.
- Explanations are paraphrased, not copied. Quotes are the politicians' own words as the outlets reported them. Square brackets mark text the outlets added, and one quote (numerus clausus) is a paraphrase by Heidi.news.
- Le Monde and Libération could not be fetched directly (blocked or paywalled). They were read through Internet Archive snapshots of the original URLs. The URLs in `refs` are the original publisher URLs.

## Articles used

| Outlet | Title | URL | Claims |
|---|---|---|---|
| Le Monde (Les Décodeurs) | Débat de l'entre-deux-tours : les affirmations de Marine Le Pen et d'Emmanuel Macron vérifiées | https://www.lemonde.fr/les-decodeurs/article/2022/04/21/debat-de-l-entre-deux-tours-les-affirmations-de-marine-le-pen-et-d-emmanuel-macron-verifiees_6123024_4355770.html | 22 |
| Le Monde (Les Décodeurs) | Notre décryptage du débat Macron-Le Pen sur les propositions, affrontements, et nos vérifications | https://www.lemonde.fr/les-decodeurs/article/2022/04/21/propositions-affrontements-et-verifications-notre-grand-recit-du-debat-macron-le-pen_6123038_4355770.html | 7 |
| franceinfo (Vrai ou fake) | Débat de la présidentielle : on a vérifié 12 affirmations d'Emmanuel Macron et Marine Le Pen | https://www.francetvinfo.fr/elections/presidentielle/vrai-ou-fake-debat-de-la-presidentielle-on-a-verifie-douze-affirmations-d-emmanuel-macron-et-marine-le-pen_5093455.html | 12 |
| Libération (CheckNews) | Débat Macron-Le Pen : des bobards et des faits (roundup) | https://www.liberation.fr/checknews/debat-macron-le-pen-des-bobards-et-des-faits-20220421_DHZYSMFQONA3RLUJTYUQ4XJAVM/ | 18 |
| Libération (CheckNews) | Débat présidentiel : 15 000 soignants ont-ils été licenciés… | https://www.liberation.fr/checknews/debat-presidentiel-15-000-soignants-ont-ils-ete-licencies-en-raison-de-la-crise-sanitaire-comme-laffirme-le-pen-20220420_3GZ436HVGFDGHF4W2CP2BJBBCU/ | 1 |
| Libération (CheckNews) | … le nombre de pauvres a-t-il augmenté de 400 000… | https://www.liberation.fr/checknews/debat-presidentiel-le-nombre-de-pauvres-a-t-il-augmente-de-400-000-durant-le-quinquennat-de-macron-comme-laffirme-le-pen-20220420_6VBCPYEZZRFGLBZEXM3KPKUTLY/ | 1 |
| Libération (CheckNews) | … Marine Le Pen d'accord sur « l'intégralité des sanctions » contre la Russie, vraiment ? | https://www.liberation.fr/checknews/debat-presidentiel-le-pen-daccord-sur-lintegralite-des-sanctions-contre-la-russie-vraiment-20220420_62UBE5OQWNHC7NOCOXVRZMUBSA/ | 1 |
| Libération (CheckNews) | … les travailleurs détachés en France sont-ils 50 000 ou plusieurs centaines de milliers ? | https://www.liberation.fr/checknews/debat-presidentiel-les-travailleurs-detaches-en-france-sont-ils-50-000-ou-plusieurs-centaines-de-milliers-20220420_F6ZJX3Y32BFDVA4EJOLP3JWSWU/ | 2 |
| Libération (CheckNews) | … l'industrie a-t-elle perdu 14 500 emplois en cinq ans… | https://www.liberation.fr/checknews/debat-presidentiel-lindustrie-a-t-elle-perdu-14-500-emplois-en-cinq-ans-comme-laffirme-marine-le-pen-20220420_QJAVDN7LDBB2LE47QRJ5JDYPSE/ | 1 |
| Libération (CheckNews) | … Macron a-t-il changé d'avis sur le nucléaire en cinq ans… | https://www.liberation.fr/checknews/debat-presidentiel-macron-a-t-il-change-davis-sur-le-nucleaire-en-cinq-ans-comme-laffirme-marine-le-pen-20220420_R2XGXE3ZYNGXLF7KAIV5THRWKM/ | 1 |
| Libération (CheckNews) | … Macron a-t-il vraiment doublé le rythme de la réduction des émissions… | https://www.liberation.fr/checknews/debat-presidentiel-macron-a-t-il-vraiment-double-le-rythme-de-la-reduction-des-emissions-de-gaz-a-effet-de-serre-20220420_GJIPKGZFFFBJZAR4ADMIJ6AVBU/ | 1 |
| Libération (CheckNews) | … Marine Le Pen est-elle climatosceptique… | https://www.liberation.fr/checknews/debat-presidentiel-marine-le-pen-est-elle-climatosceptique-20220420_AZ55DS2LURCXLIAR2OJ5FYTE2U/ | 1 |
| Libération (CheckNews) | … quelle était la position de Marine Le Pen sur l'Ukraine en 2014 ? | https://www.liberation.fr/checknews/debat-presidentiel-quelle-etait-la-position-de-marine-le-pen-sur-lukraine-en-2014-20220420_Q4XOTD67JFASXJ7GZJQVEGTJF4/ | 1 |
| Libération (CheckNews) | … comment a évolué le taux de chômage et le nombre de demandeurs d'emplois sous Macron ? | https://www.liberation.fr/checknews/debat-presidentielle-comment-a-evolue-le-taux-de-chomage-et-le-nombre-de-demandeurs-demplois-sous-macon-20220420_XRV7LL5WMFHRNHQRAPMBLYCKXQ/ | 2 |
| Libération (CheckNews) | … quelle est la part du Covid dans la hausse de 600 milliards d'euros de dette… | https://www.liberation.fr/checknews/debat-presidentielle-quelle-est-la-part-du-covid-dans-la-hausse-de-600-milliards-deuros-de-dette-sous-macron-20220420_VWTHXPY2SREBDIK5O7XG54ZI6U/ | 1 |
| Libération (CheckNews) | … une personne sur deux est-elle inactive au moment de prendre sa retraite… | https://www.liberation.fr/checknews/debat-presidentielle-une-personne-sur-deux-est-elle-inactive-au-moment-de-prendre-sa-retraite-comme-laffirme-marine-le-pen-20220420_DJBZXXOL3FD4TOX4K5RP2XNCWU/ | 1 |
| Libération (CheckNews) | Est-il possible de mettre en place une TVA à 5,5 % sur les carburants « de manière pérenne »… | https://www.liberation.fr/checknews/est-il-possible-de-mettre-en-place-une-tva-a-55-sur-les-carburants-de-maniere-perenne-comme-le-souhaite-le-pen-20220420_ETXYJUODS5BIJPB5SBQJZZ4QDY/ | 1 |
| Libération (CheckNews) | La hausse des prix du carburant est-elle la responsabilité de Macron… | https://www.liberation.fr/checknews/la-hausse-des-prix-du-carburant-est-elle-la-responsabilite-de-macron-comme-laffirme-le-pen-20220420_OKDYU467EFA2TEPTAPBBTF3AEA/ | 1 |
| Libération (CheckNews) | L'UE attend-elle le second tour… pour annoncer un embargo sur le pétrole russe… | https://www.liberation.fr/checknews/lue-attend-elle-le-second-tour-de-la-presidentielle-pour-annoncer-un-embargo-sur-le-petrole-russe-comme-laffirme-marine-le-pen-20220420_KODTR5H3O5DWXKJJEAYEVQODZY/ | 1 |
| Libération (CheckNews) | Pouvoir d'achat : qui sont les gagnants et les perdants du quinquennat Macron ? | https://www.liberation.fr/checknews/pouvoir-dachat-qui-sont-les-gagnants-et-les-perdants-du-quinquennat-macron-20220420_OSWJHWLQ7VBJVD4OYJW6UKLZJU/ | 1 |
| Libération (CheckNews) | Y a-t-il 10 000 policiers et gendarmes de plus sur le terrain depuis cinq ans… | https://www.liberation.fr/checknews/y-a-t-il-10-000-policiers-et-gendarmes-de-plus-sur-le-terrain-depuis-cinq-ans-comme-le-dit-emmanuel-macron-20220420_5YZUHL2DRVESFDQ3TI3YGFTDXA/ | 1 |
| AFP Factuel | Dette, chômage, et TVA... Retour sur les affirmations contestées du débat Macron-Le Pen | https://factuel.afp.com/doc.afp.com.328N4MJ (also mirrored at https://defacto-observatoire.fr/Medias/Factuel/Fact-checks/Dette-chomage-et-TVA-Retour-sur-les-affirmations-contestees-du-debat-Macron-Le-Pen) | 9 |
| TF1 Info (Les Vérificateurs) | REVIVEZ - Présidentielle : le débat d'entre-deux-tours Macron-Le Pen avec nos Vérifications | https://www.tf1info.fr/international/direct-video-debat-macron-le-pen-tf1-suivez-ce-temps-fort-de-la-campagne-presidentielle-2022-avec-le-fact-checking-de-nos-verificateurs-2217236.html | 18 |
| 20 Minutes (Fake Off) | Présidentielle 2022 : Ukraine, éoliennes, pauvreté… Notre fact-checking des déclarations d'Emmanuel Macron et Marine Le Pen lors du débat | https://www.20minutes.fr/elections/3274907-20220421-presidentielle-2022-ukraine-eoliennes-pauvrete-fact-checking-declarations-emmanuel-macron-marine-pen-lors-debat | 13 |
| Le JDD | Débat Le Pen-Macron : on a vérifié cinq affirmations des candidats | https://www.lejdd.fr/Politique/debat-le-pen-macron-on-a-verifie-cinq-affirmations-des-candidats-4106912 | 6 |
| Le Figaro | Immigration, Europe, sécurité, chômage, inflation... Nous avons vérifié dix affirmations du débat Macron-Le Pen (paywalled, only first 2 items readable) | https://www.lefigaro.fr/conjoncture/chomage-inflation-pge-nous-avons-verifie-cinq-affirmations-du-debat-macron-le-pen-20220421 | 2 |
| Ouest-France | Dette publique, Europe, bouclier énergétique… On a vérifié six affirmations du débat | https://www.ouest-france.fr/elections/presidentielle/presidentielle-dette-publique-europe-bouclier-energetique-on-a-verifie-six-affirmations-du-debat-e426f182-c144-11ec-9f1f-583f976b4bb7 | 3 |
| Ouest-France | Débat Macron-Le Pen : qu'en est-il vraiment du parc éolien du Touquet ? | https://www.ouest-france.fr/elections/presidentielle/presidentielle-debat-macron-le-pen-qu-en-est-il-vraiment-du-parc-eolien-du-touquet-eeabd37e-c14a-11ec-8a48-02f9bba22954 | 1 |
| BFMTV | LA VÉRIF - Éoliennes, chômage, agressions... 3 séquences du débat Le Pen-Macron passées au crible | https://www.bfmtv.com/politique/elections/presidentielle/la-verif-eoliennes-chomage-agressions-3-sequences-du-debat-le-pen-macron-passees-au-crible_AN-202204210452.html | 4 |
| BFMTV | Débat d'entre-deux-tours : Macron a-t-il vraiment suspendu le projet d'éoliennes en face du Touquet ? | https://www.bfmtv.com/economie/entreprises/energie/debat-d-entre-deux-tours-macron-a-t-il-vraiment-suspendu-le-projet-d-eoliennes-en-face-du-touquet_AV-202204200698.html | 1 |
| L'Obs | Débat Macron-Le Pen : qui dit vrai sur le niveau du chômage ? | https://www.nouvelobs.com/election-presidentielle-2022/20220420.OBS57387/debat-macron-le-pen-qui-dit-vrai-sur-le-niveau-du-chomage.html | 2 |
| Heidi.news (CH) | Le débat Le Pen - Macron vérifié par Heidi.news | https://www.heidi.news/articles/en-direct-le-debat-le-pen-macron-verifie-par-heidi-news | 6 |
| RTBF Faky (BE, secondary roundup) | Présidentielle 2022 : le vrai du faux des affirmations des candidats lors du débat Macron – Le Pen | https://www.rtbf.be/article/presidentielle-2022-le-vrai-du-faux-des-affirmations-des-candidats-lors-du-debat-macron-le-pen-10978340 | 28 |

## Gaps

- No dedicated fact-check of this debate was found, or one could not be retrieved, for:
  - Les Echos
  - Public Sénat
  - France 24
  - La Croix
  - Alternatives Économiques
  - Le Parisien (only a recap of highlights)
  - Europe 1, Le Point, L'Express, Marianne
- Le Figaro's 10-point article is paywalled. Only the inflation and "1,2 million d'emplois" items were readable.
- Le Monde, Libération and Le Figaro claim counts reflect the snapshots that were accessible. Le Monde's article was updated during 21 April: the 18h57 version adds the two climate items.
- A few Heidi.news items had no verbatim quote and were left out: small shops during Covid, European data protection, and wind-turbine dismantling.
- Several CheckNews pieces published the same day concerned other debates (Bardella/Véran) and were excluded.
