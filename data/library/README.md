# Bibliothèque de vérifications pré-débat : présidentielle 2022 (Macron / Le Pen)

Fichier : `2022-presidential.jsonl` (une affirmation récurrente vérifiée par ligne, voir le schéma dans la demande d'origine).

Contrainte : toutes les références sont publiées **strictement avant le 20 avril 2022** (date du débat d'entre-deux-tours). La référence la plus récente date du 2022-04-17 ; la plus ancienne du 2014-11-23.

## Totaux

- Entrées : **91**  (Macron : 39, Le Pen : 52)
- Verdicts : faux 24, trompeur 23, imprecis 21, exact 13, plutot_exact 9, inverifiable 1
- Thèmes : immigration 11, énergie 10, institutions 9, Russie/Ukraine 7, emploi 6, chômage 5, climat 4, fiscalité 4, pouvoir d'achat 4, retraites 4, sécurité 4, santé 4, éducation 3, logement 2, social 2, Europe 2, laïcité 2, attractivité 1, croissance 1, défense 1, quoi qu'il en coûte 1, agriculture 1, culture 1, dette 1, justice 1
- Articles distincts cités : 83

## Notes de méthode

- Les sites lemonde.fr, liberation.fr, tf1info.fr, factuel.afp.com et 20minutes.fr n'étaient pas accessibles à l'outil de collecte (blocage du robot) ; ils n'ont pas été contournés. Les fact-checks AFP Factuel sont cités via leur republication intégrale créditée AFP sur imazpress.com ; quelques vérifications franceinfo/LCI sont citées via Factoscope.
- Certaines affirmations sont portées par des porte-parole (Bardella pour le RN ; ministres ou porte-parole du gouvernement pour le bilan Macron) quand il s'agit d'éléments de langage repris par le candidat ; l'attribution est précisée dans `key_facts` ou `explanation` le cas échéant.
- Quand l'article ne donne pas d'étiquette, le verdict a été déduit de sa conclusion (`label` vide dans `refs`).
- Points de date à surveiller : l'article Euronews sur Le Pen et la Russie est publié le 17/04/2022 mais mis à jour le 20/04/2022 à 21h53 (le contenu ne mentionne pas le débat) ; l'article Mediacités sur les émissions (-12 %) est publié le 15/04/2022 et modifié le 20/04/2022 ; la date de l'article Mediacités MaPrimeRénov (06/04/2022) provient de la page de liste de la rubrique ; l'article franceinfo sur le pouvoir d'achat (08/10/2021) a été mis à jour le 16/12/2021. Trois entrées s'appuient sur des références antérieures à la campagne (2014 pour le prêt russe, 2020 pour le voile et les « 44 secondes »), l'affirmation ayant été répétée en 2021-2022.
- Lacunes : aucune vérification accessible et antérieure au 20/04/2022 n'a été trouvée sur les taux d'exécution des OQTF, le bilan de députée de Marine Le Pen (présence, votes), ses votes au Parlement européen, le minimum retraite à 1 100 €, la suppression de l'ISF, le coût du bouclier tarifaire ou le vote du RN sur ce dernier. Le prêt russe n'est couvert que par un article factuel de 2014.

## Articles utilisés

| Média | Date | URL | Entrées |
|---|---|---|---|
| franceinfo | 2014-11-23 | https://www.franceinfo.fr/monde/russie/le-fn-a-emprunte-neuf-millions-d-euros-a-une-banque-russe_752203.html | L071 |
| Factoscope | 2020-09-01 | https://factoscope.fr/factoscope1722/il-y-a-une-agression-gratuite-toutes-les-44-secondes-dans-notre-pays/ | L081 |
| Les Surligneurs | 2020-10-27 | https://lessurligneurs.eu/marine-le-pen-reclame-linterdiction-du-voile-dans-lespace-public/ | L065 |
| franceinfo (Vrai ou fake) | 2021-06-04 | https://www.franceinfo.fr/environnement/crise-climatique/vrai-ou-fake-regionales-les-attaques-de-certains-candidats-contre-les-eoliennes-sont-elles-fondees_4646077.html | L083, L084 |
| Factoscope (d'après LCI Les Vérificateurs) | 2021-06-07 | https://factoscope.fr/factoscope1722/il-y-a-dans-notre-pays-une-agression-gratuite-toutes-les-44-secondes/ | L081 |
| franceinfo (Vrai ou fake) | 2021-07-23 | https://www.franceinfo.fr/economie/emploi/vrai-ou-fake-emmanuel-macron-a-t-il-raison-d-affirmer-qu-il-est-possible-de-mieux-gagner-sa-vie-en-restant-chez-soi-plutot-qu-en-travaillant_4700447.html | L002 |
| Les Surligneurs | 2021-09-26 | https://lessurligneurs.eu/reserver-les-logements-sociaux-aux-francais-en-depit-du-principe-degalite-la-promesse-de-marine-le-pen-qui-interroge/ | L051 |
| AFP Factuel (republié par Imaz Press) | 2021-09-27 | https://imazpress.com/factchecking/le-logement-social-finance-par-les-francais-laffirmation-inexacte-de-marine-le-pen | L052 |
| AFP Factuel (republié par Imaz Press) | 2021-09-28 | https://imazpress.com/factchecking/non-la-france-naccueille-pas-sur-son-territoire-lequivalent-de-la-ville-de-lyon-chaque-annee | L053 |
| Public Sénat | 2021-09-29 | https://www.publicsenat.fr/actualites/politique/immigration-le-referendum-voulu-par-marine-le-pen-est-il-constitutionnel-190598 | L051 |
| franceinfo (Vrai ou fake) | 2021-09-29 | https://www.franceinfo.fr/societe/immigration/vrai-ou-fake-presidentielle-2022-le-referendum-sur-l-immigration-propose-par-marine-le-pen-xavier-bertrand-ou-michel-barnier-serait-il-conforme-a-la-constitution_4786337.html | L051 |
| franceinfo (Vrai ou fake) | 2021-10-08 | https://www.franceinfo.fr/politique/emmanuel-macron/vrai-ou-fake-le-pouvoir-dachat-a-t-il-plus-augmente-sous-le-quinquennat-demmanuel-macron-que-sous-ceux-de-ses-predecesseurs_4797307.html | L026 |
| franceinfo (Vrai ou fake) | 2021-10-11 | https://www.franceinfo.fr/elections/presidentielle/vrai-ou-fake-presidentielle-2022-on-a-verifie-cinq-declarations-sur-l-immigration-lancees-au-cours-de-la-campagne_4798413.html | L054 |
| AFP Factuel (republié par Imaz Press) | 2021-10-13 | https://imazpress.com/factchecking/les-francais-travaillent-moins-que-leurs-voisins-les-limites-de-laffirmation-demmanuel-macron-2 | L010 |
| franceinfo (Vrai ou fake) | 2021-10-15 | https://www.franceinfo.fr/elections/presidentielle/vrai-ou-fake-presidentielle-2022-on-a-verifie-cinq-affirmations-du-tract-de-la-majorite-sur-le-bilan-economique-d-emmanuel-macron_4806011.html | L001, L003, L014, L027, L028 |
| franceinfo (Vrai ou fake) | 2021-10-16 | https://www.franceinfo.fr/elections/presidentielle/vrai-ou-fake-l-immigration-tire-t-elle-les-salaires-vers-le-bas-comme-l-affirme-marine-le-pen_4808793.html | L055 |
| Le JDD | 2021-10-17 | https://www.lejdd.fr/Politique/le-pouvoir-dachat-a-t-il-vraiment-augmente-sous-macron-4071979 | L026 |
| France Bleu (ici) | 2021-11-16 | https://www.ici.fr/infos/economie-social/marine-le-pen-veut-rouvrir-la-centrale-nucleaire-de-fessenheim-difficilement-envisageable-pour-l-asn-1637016946 | L085 |
| AFP Factuel (republié par Imaz Press) | 2021-11-17 | https://imazpress.com/factchecking/attention-aux-interpretations-trompeuses-des-chiffres-de-ce-rapport-de-latih | L077 |
| Boursorama (d'après Capital) | 2021-11-25 | https://www.boursorama.com/epargne/retraite/actualites/les-jeunes-de-moins-de-30-ans-pourront-ils-etre-exoneres-d-impots-comme-le-propose-marine-le-pen-19110daa79c8b661a7badc004d9a1586 | L049 |
| AFP (via Arab News en français) | 2021-12-02 | https://www.arabnews.fr/france/allocations-et-immigration-le-pen-detaille-les-16-milliards-deuros-par-an-quelle-entend-economiser-175171 | L056 |
| franceinfo (Vrai ou fake) | 2021-12-02 | https://www.franceinfo.fr/economie/emploi/chomage/vrai-ou-fake-le-gouvernement-affirme-que-le-chomage-est-au-plus-bas-depuis-15-ans_4866941.html | L003 |
| franceinfo (Vrai ou fake) | 2021-12-16 | https://www.franceinfo.fr/environnement/energie/nucleaire/vrai-ou-fake-surete-cout-nouveaux-epr-on-a-verifie-les-affirmations-de-six-candidats-a-la-presidentielle-sur-le-nucleaire_4875187.html | L086 |
| franceinfo (Le vrai du faux) | 2021-12-17 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/le-chomage-au-plus-bas-depuis-15-ans-est-ce-un-mensonge-comme-le-dit-julien-odoul_4867385.html | L003, L041 |
| AFP Factuel (republié par Imaz Press) | 2022-01-08 | https://imazpress.com/factchecking/un-milliard-deuros-contre-les-violences-faites-aux-femmes-le-chiffre-difficilement-verifiable | L033 |
| AFP Factuel (republié par Imaz Press) | 2022-01-10 | https://imazpress.com/factchecking/moins-delus-fn-sous-le-quinquennat-demmanuel-macron-que-sous-francois-hollande-les-limites-de-la-demonstration-de-gabriel-attal | L017 |
| franceinfo (Le vrai du faux) | 2022-01-12 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/les-emissions-de-co2-dans-la-production-d-energie-ont-elles-baisse-de-38-depuis-2017-comme-l-affirme-barbara-pompili_4897009.html | L006 |
| franceinfo (Vrai ou fake) | 2022-01-13 | https://www.franceinfo.fr/economie/emploi/metiers/armee-et-securite/vrai-ou-fake-emmanuel-macron-a-t-il-vraiment-cree-10-000-postes-de-policiers-et-gendarmes_4915651.html | L034 |
| franceinfo (Le vrai du faux) | 2022-01-18 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/est-ce-que-la-france-est-le-2e-pays-le-plus-propre-au-monde-grace-au-nucleaire-comme-le-dit-jordan-bardella_4903647.html | L087 |
| AFP Factuel (republié par Imaz Press) | 2022-01-25 | https://imazpress.com/factchecking/pas-demeutes-urbaines-depuis-2017-dixit-darmanin-mais-de-nombreuses-violences-et-des-gilets-jaunes-inedits | L035 |
| franceinfo (Vrai ou fake) | 2022-01-27 | https://www.franceinfo.fr/economie/emploi/chomage/vrai-ou-fake-la-france-a-t-elle-cree-un-million-d-emplois-depuis-2017-comme-l-affirme-bruno-le-maire_4929021.html | L011, L016 |
| France 24 | 2022-01-28 | https://www.france24.com/fr/%C3%A9missions/info-%C3%A9co/20220128-france-le-taux-de-croissance-au-plus-haut-depuis-1969 | L008 |
| Public Sénat | 2022-01-29 | https://www.publicsenat.fr/actualites/politique/croissance-a-7-c-est-une-forme-de-rattrapage-tempere-le-rapporteur-de-la | L008 |
| franceinfo | 2022-02-02 | https://www.franceinfo.fr/economie/emploi/carriere/formation/l-apprentissage-enregistre-un-nouveau-record-en-2021-avec-plus-de-700000-contrats-signes_4938720.html | L012 |
| franceinfo (Le vrai du faux) | 2022-02-14 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/covid-la-france-fait-elle-partie-des-10-de-pays-qui-ont-le-moins-ferme-l-ecole_4935451.html | L036 |
| AFP Factuel (republié par Imaz Press) | 2022-02-17 | https://imazpress.com/factchecking/le-francais-nest-pas-la-deuxieme-langue-la-plus-parlee-dans-le-monde-comme-laffirme-marine-le-pen | L043 |
| franceinfo | 2022-02-18 | https://www.franceinfo.fr/economie/emploi/chomage/pourquoi-le-chomage-des-jeunes-est-il-au-plus-bas_4968669.html | L004 |
| franceinfo (Le vrai du faux) | 2022-02-21 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/la-france-affiche-t-elle-un-taux-d-emploi-des-jeunes-dix-points-inferieur-a-la-moyenne-de-l-ocde-comme-l-affirme-marine-le-pen_4946733.html | L045 |
| Les Surligneurs | 2022-02-24 | https://lessurligneurs.eu/marine-le-pen-souhaite-retablir-les-controles-aux-frontieres-tout-en-prevoyant-des-procedures-simplifiees-pour-les-citoyens-europeens/ | L047 |
| France 24 | 2022-02-28 | https://www.france24.com/fr/france/20220228-guerre-en-ukraine-volte-face-et-ambigu%C3%AFt%C3%A9s-des-candidats-m%C3%A9lenchon-roussel-le-pen-et-zemmour | L072 |
| franceinfo (Vrai ou fake) | 2022-03-04 | https://www.franceinfo.fr/politique/marine-le-pen/vrai-ou-fake-presidentielle-on-a-verifie-neuf-affirmations-de-marine-le-pen-dans-elysee-2022_4990870.html | L003, L041, L044, L046, L069, L070, L075, L076, L088, L089 |
| AFP Factuel (republié par Imaz Press) | 2022-03-08 | https://imazpress.com/factchecking/trois-fois-plus-de-femmes-a-lassemblee-depuis-2017-la-grosse-exageration-demmanuel-macron | L018 |
| franceinfo (Le vrai du faux) | 2022-03-08 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/guerre-en-ukraine-emmanuel-macron-est-il-le-seul-chef-d-etat-a-discuter-avec-vladimir-poutine_4974969.html | L031 |
| LCP | 2022-03-15 | https://lcp.fr/actualites/2017-2022-le-president-a-t-il-tenu-les-engagements-du-candidat-106435 | L009, L012, L015, L037 |
| franceinfo (Le vrai du faux) | 2022-03-15 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/marine-le-pen-dit-elle-vrai-sur-les-differences-entre-refugies-syriens-et-ukrainiens_4984794.html | L057 |
| AFP Factuel (republié par Imaz Press) | 2022-03-16 | https://imazpress.com/factchecking/pas-daleas-dans-le-nucleaire-attention-a-cette-affirmation-demmanuel-macron | L039 |
| franceinfo (Le vrai du faux) | 2022-03-16 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/pouvoir-dachat-emmanuel-macron-est-il-responsable-de-la-hausse-du-prix-des-carburants-comme-laffirme-marine-le-pen_4988412.html | L067 |
| AFP Factuel (republié par Imaz Press) | 2022-03-17 | https://imazpress.com/factchecking/emmanuel-macron-a-t-il-double-la-production-de-charbon-comme-laffirme-marine-le-pen | L090 |
| AFP Factuel (republié par Imaz Press) | 2022-03-18 | https://imazpress.com/factchecking/des-depenses-pour-des-cabinets-de-conseil-deja-en-baisse-de-15-selon-macron-une-promesse-qui-reste-a-tenir | L019 |
| AFP Factuel (republié par Imaz Press) | 2022-03-21 | https://imazpress.com/factchecking/les-raccourcis-demmanuel-macron-autour-de-la-mise-en-examen-deric-dupond-moretti | L020 |
| franceinfo (Le vrai du faux) | 2022-03-22 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/l-indemnite-inflation-a-t-elle-ete-versee-a-des-des-gens-qui-n-ont-pas-de-voiture-et-des-detenus-comme-l-affirme-marine-le-pen_4997667.html | L068 |
| AFP Factuel (republié par Imaz Press) | 2022-03-25 | https://imazpress.com/factchecking/bataille-et-querelles-de-chiffres-autour-de-la-baisse-du-chomage | L004, L005 |
| franceinfo (Le vrai du faux) | 2022-03-25 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/un-etranger-en-situation-irreguliere-beneficie-t-il-de-l-integralite-de-la-palette-de-soins-gratuite-grace-a-l-ame-comme-l-affirme-jordan-bardella_5003870.html | L078 |
| franceinfo | 2022-03-26 | https://www.franceinfo.fr/elections/presidentielle/presidentielle-2022-on-a-verifie-cinq-affirmations-des-candidats-sur-les-retraites_5036391.html | L029 |
| franceinfo | 2022-03-29 | https://www.franceinfo.fr/elections/programmes-election-presidentielle-2022/programmes-climat/presidentielle-le-programme-de-marine-le-pen-est-il-dans-les-clous-des-objectifs-climatiques-de-la-france_5024636.html | L042 |
| franceinfo (Le vrai du faux) | 2022-03-30 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/presidentielle-2022-on-a-verifie-trois-declarations-de-candidats-sur-l-agriculture_5016371.html | L040 |
| AFP Factuel (republié par Imaz Press) | 2022-04-01 | https://imazpress.com/factchecking/emmanuel-macron-et-les-cabinets-de-conseils-vrais-et-faux-arguments-dune-polemique-de-campagne | L022, L023 |
| Les Surligneurs | 2022-04-01 | https://lessurligneurs.eu/marine-le-pen-je-vais-reserver-un-certain-nombre-de-prestations-comme-les-allocations-familiales-exclusivement-aux-francais/ | L059 |
| Mediacités (Véracités 2022) | 2022-04-01 | https://www.mediacites.fr/veracites-2022/probite-des-elus-jai-pris-des-engagements-et-je-les-ai-remplis/ | L021 |
| franceinfo (Le vrai du faux) | 2022-04-01 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/presidentielle-2022-on-a-verifie-trois-declarations-de-candidats-sur-l-immigration_5022446.html | L058 |
| Factoscope (d'après franceinfo Vrai ou fake) | 2022-04-02 | https://factoscope.fr/factoscope1722/93-des-vols-sans-violence-dans-les-transports-en-ile-de-france-sont-le-fait-detrangers/ | L082 |
| AFP Factuel (republié par Imaz Press) | 2022-04-04 | https://imazpress.com/factchecking/apprentissage-des-12-ans-vraie-proposition-de-macron-ou-fake-news-de-melenchon | L038 |
| franceinfo (Le vrai du faux) | 2022-04-04 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/presidentielle-2022-on-a-verifie-trois-declarations-de-candidats-sur-les-questions-de-police-et-de-justice_5031301.html | L064 |
| AFP Factuel (republié par Imaz Press) | 2022-04-05 | https://imazpress.com/factchecking/une-tva-a-55-sur-les-carburants-compatible-avec-le-droit-europeen-laffirmation-erronee-de-marine-le-pen | L050 |
| Mediacités (Véracités 2022) | 2022-04-06 | https://www.mediacites.fr/veracites-2022/maprimerenov-ca-marche-tres-bien-nous-avons-reussi-en-2021-a-financer-650-000-logements/ | L024 |
| franceinfo (Le vrai du faux) | 2022-04-06 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/presidentielle-2022-on-a-verifie-trois-declarations-de-candidats-sur-la-sante_5037306.html | L079, L080 |
| franceinfo (Vrai ou fake) | 2022-04-06 | https://www.franceinfo.fr/elections/presidentielle/candidats/presidentielle-on-a-verifie-six-affirmations-des-candidats-invites-dans-l-emission-elysee-2022_5064190.html | L079 |
| France 3 Centre-Val de Loire | 2022-04-07 | https://france3-regions.franceinfo.fr/centre-val-de-loire/presidentielle-2022-faut-il-se-satisfaire-du-taux-de-chomage-des-jeunes-au-plus-bas-depuis-20-ans-2520104.html | L004 |
| franceinfo (Le vrai du faux) | 2022-04-07 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/presidentielle-2022-on-a-verifie-trois-declarations-de-candidats-sur-l-energie_5040461.html | L091 |
| AFP Factuel (republié par Imaz Press) | 2022-04-08 | https://imazpress.com/factchecking/non-les-creations-demplois-durant-le-quinquennat-demmanuel-macron-ne-sont-pas-du-jamais-vu-dans-leconomie-francaise | L013 |
| InfoMigrants | 2022-04-11 | https://www.infomigrants.net/fr/post/39776/presidentielle-2022--que-proposent-macron-et-le-pen-sur-limmigration | L060, L061 |
| AFP Factuel (republié par Imaz Press) | 2022-04-13 | https://imazpress.com/factchecking/les-explications-contestables-demmanuel-macron-sur-les-origines-de-la-baisse-des-apl-de-2017 | L025 |
| AFP Factuel (republié par Imaz Press) | 2022-04-13 | https://imazpress.com/factchecking/marine-le-pen-et-la-priorite-nationale-le-conseil-constitutionnel-peut-il-sopposer-a-un-referendum-contournant-le-parlement | L062 |
| franceinfo (Le vrai du faux) | 2022-04-13 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/le-non-recours-aux-prestations-sociales-represente-t-il-des-milliards-deuros-comme-laffirme-richard-ferrand_5052076.html | L032 |
| INA (Ina éclaire l'actu) | 2022-04-14 | https://www.ina.fr/ina-eclaire-actu/marine-le-pen-fn-russie-ukraine-rn-politique-relation | L072 |
| Les Surligneurs | 2022-04-14 | https://lessurligneurs.eu/marine-le-pen-veut-reviser-la-constitution-par-referendum-en-contournant-le-parlement/ | L063 |
| Public Sénat | 2022-04-14 | https://www.publicsenat.fr/actualites/non-classe/sur-l-europe-marine-le-pen-a-t-elle-vraiment-change-202482 | L048 |
| Public Sénat | 2022-04-14 | https://www.publicsenat.fr/actualites/politique/revision-de-la-constitution-par-referendum-marine-le-pen-dans-une-impasse | L063 |
| franceinfo (Le vrai du faux) | 2022-04-14 | https://www.franceinfo.fr/replay-radio/le-vrai-du-faux/habib-bourguiba-avait-il-interdit-le-voile-dans-la-rue-comme-l-affirme-marine-le-pen_5054044.html | L066 |
| AFP Factuel (republié par Imaz Press) | 2022-04-15 | https://imazpress.com/factchecking/faut-il-sauver-le-systeme-des-retraites-les-projections-sont-moins-alarmistes | L030 |
| Le JDD | 2022-04-15 | https://www.lejdd.fr/Politique/credibilite-efficacite-legalite-un-economiste-evalue-les-deux-baisses-de-tva-de-marine-le-pen-4105789 | L050 |
| Mediacités (Véracités 2022) | 2022-04-15 | https://www.mediacites.fr/veracites-2022/javais-dit-que-nous-reduirons-nos-emissions-de-gaz-a-effet-de-serre-nous-lavons-fait-deux-fois-plus-vite-quauparavant-12-durant-ce-quinquennat/ | L007 |
| Euronews | 2022-04-17 | https://www.euronews.com/my-europe/2022/04/18/french-election-what-exactly-is-marine-le-pen-s-stance-on-russia-and-vladimir-putin | L073, L074 |
