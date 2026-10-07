# Évaluation — 2022-macron-lepen

Comparaison avec les vérifications publiées par les rédactions (voir `data/gold/`).

| Mesure | Valeur |
|---|---|
| Affirmations vérifiées par les rédactions | 56 |
| … détectées par Sic (rappel) | 48 (86%) |
| Verdict identique | 31% |
| Même catégorie (juste / problème / invérifiable) | 46% |
| Même catégorie, hors « invérifiable » de Sic | 70% |
| « Invérifiable » chez Sic sur ces affirmations | 18 |
| Erreurs critiques (juste ↔ faux) | 1 |
| Affirmations extraites par Sic (total) | 279 |
| Latence de vérification p50 / p90 | 58.81 s / 217.05 s |

## Détail

| Orateur | Affirmation (rédactions) | Verdict rédactions | Verdict Sic | Titre Sic |
|---|---|---|---|---|
| lepen | Pendant le quinquennat d'Emmanuel Macron, les pensions de retraite n'ont pas été revalorisées au niveau de l'i | plutot_exact | inverifiable | Non-respect de l'indexation des retraites 2017-2022 : pas tranchable avec les so |
| lepen | Au moment du débat d'avril 2022, l'inflation en France dépassait la croissance d'au moins un point, condition  | faux | inverifiable | Chiffres attribués à la Banque de France non confirmés par les sources accessibl |
| lepen | La France peut abaisser durablement la TVA sur toutes les énergies, carburants compris, de 20 % à 5,5 %. | trompeur | non détectée |  |
| lepen | La baisse de la TVA de 20 % à 5,5 % sur l'énergie représenterait environ 12 milliards d'euros rendus aux ménag | exact | non détectée |  |
| lepen | Le nombre de personnes sous le seuil de pauvreté a augmenté de 400 000 sous le quinquennat Macron et atteint 9 | plutot_exact | imprecis | Les 400 000 pauvres en plus correspondent à 2017-2018, pas au quinquennat entier |
| lepen | Le chômage n'a pas baissé pendant le quinquennat d'Emmanuel Macron. | faux | non détectée |  |
| lepen | Le nombre de demandeurs d'emploi inscrits à Pôle emploi en catégories A, B et C est passé d'environ 5,5 millio | imprecis | plutot_exact | Environ 5,5 millions de demandeurs d'emploi en catégories A, B, C à l'élection d |
| lepen | L'industrie française a perdu 14 500 emplois pendant le quinquennat d'Emmanuel Macron. | faux | faux | L'emploi industriel a augmenté sous le quinquennat, pas diminué : +30 000 à +48  |
| lepen | Au moment où ils partent à la retraite, environ la moitié des Français ne sont déjà plus en emploi. | plutot_exact | inverifiable | Aucune source exploitable obtenue sur l'inactivité avant le départ en retraite à |
| lepen | La dette publique a augmenté d'environ 600 milliards d'euros sous Emmanuel Macron, dont les deux tiers sans li | imprecis | imprecis | Sur ~600 Md€ de dette en plus, environ 165 Md€ seulement sont liés au Covid |
| lepen | Le gouvernement a licencié 15 000 soignants non vaccinés contre le Covid-19. | trompeur | inverifiable | Suspensions de soignants non vaccinés : aucun chiffre fiable obtenu |
| lepen | Des centaines de milliers de travailleurs détachés viennent travailler en France. | faux | inverifiable | Détachés : cotisations sociales payées dans le pays d'origine, principe confirmé |
| lepen | Emmanuel Macron a fait valider tous les projets d'éoliennes en mer sauf celui situé face au Touquet, où il pos | faux | inverifiable | Statut des projets éoliens en mer face au Touquet non vérifiable faute de source |
| lepen | Les émissions liées aux importations représentent environ la moitié de l'empreinte carbone de la France. | plutot_exact | plutot_exact | Les émissions importées représentent près de la moitié de l'empreinte carbone de |
| lepen | Seulement environ 10 % des obligations de quitter le territoire français (OQTF) sont exécutées. | plutot_exact | plutot_exact | 7,8 % en 2020, 14 % en 2019 : le taux d'exécution des OQTF tourne autour de 10 % |
| lepen | En France, une agression gratuite est commise dans la rue toutes les 44 secondes. | imprecis | imprecis | Une violence physique hors ménage toutes les 44 secondes, mais pas « dans la rue |
| lepen | Emmanuel Macron a retiré les mathématiques du tronc commun au lycée avant de vouloir les y réintroduire. | exact | inverifiable | La réforme du lycée a sorti les maths du tronc commun dès 2019 |
| lepen | Emmanuel Macron propose de rémunérer les enseignants selon les résultats de leurs élèves. | faux | inverifiable | Aucune source exploitable obtenue sur la réforme salariale des enseignants propo |
| lepen | Emmanuel Macron aurait pu introduire la proportionnelle aux législatives pendant son quinquennat sans dépendre | exact | exact | La proportionnelle promise après les Gilets jaunes n'a jamais été mise en œuvre |
| lepen | Marine Le Pen peut réviser la Constitution par référendum via l'article 11, sans passer par le Parlement. | trompeur | exact | De Gaulle a bien révisé la Constitution par référendum via l'article 11 en 1962 |
| lepen | L'Union européenne attend la fin de l'élection présidentielle française pour décider d'un embargo sur le pétro | inverifiable | inverifiable | Aucune source exploitable trouvée sur le calendrier d'un éventuel embargo europé |
| lepen | La hausse des prix des carburants sous le quinquennat est principalement due aux hausses de taxes décidées par | trompeur | plutot_exact | Les Gilets jaunes sont nés de la hausse de la fiscalité carburant, dont la traje |
| lepen | Emmanuel Macron a instauré la taxe carbone sur les carburants. | faux | trompeur | Taxes carbone relevées en 2017-2018, mais gelées depuis fin 2018 |
| lepen | Environ 20 % des élèves ne maîtrisent pas les savoirs fondamentaux à la fin de l'école primaire. | imprecis | inverifiable | Aucune source exploitable obtenue sur le niveau des élèves en 6e |
| lepen | La suppression de l'ISF et la flat tax ont été les plus grosses baisses d'impôts du quinquennat Macron. | faux | faux | Suppression de la taxe d'habitation : 17,6 Md€/an, loin devant la fin de l'ISF ( |
| lepen | Emmanuel Macron a remplacé le drapeau français par le drapeau européen sous l'Arc de Triomphe. | faux | trompeur | Drapeau européen sous l'Arc de Triomphe : une seule nuit (31 déc.–1er jan.), pou |
| lepen | Emmanuel Macron a reçu Vladimir Poutine en grande pompe au château de Versailles. | plutot_exact | exact | Macron a bien reçu Poutine au château de Versailles le 29 mai 2017 |
| lepen | Marine Le Pen et son parti soutiennent l'ensemble des sanctions européennes contre la Russie, hormis l'embargo | trompeur | inverifiable | Position de Le Pen sur chaque sanction russe non documentée par les sources obte |
| lepen | Emmanuel Macron a changé de position sur le nucléaire au cours de son quinquennat, passant de fermetures de ré | plutot_exact | exact | Fermeture de 14 réacteurs annoncée en 2018, Astrid abandonné en 2019 : confirmé |
| lepen | Le programme de Marine Le Pen n'implique pas de sortie de l'Union européenne. | trompeur | non détectée |  |
| lepen | La majorité d'Emmanuel Macron s'est opposée à la déconjugalisation de l'allocation aux adultes handicapés (AAH | exact | exact | La majorité LREM a fait rejeter la déconjugalisation de l'AAH en mars 2019 |
| lepen | Le chèque inflation de 100 euros a été versé à des détenus. | imprecis | imprecis | Seuls les détenus en formation rémunérée ont touché l'indemnité de 100 euros |
| macron | Marine Le Pen a voté contre le bouclier tarifaire sur le gaz et l'électricité à l'Assemblée nationale. | plutot_exact | plutot_exact | Le Pen a voté contre le budget 2022, loi qui portait le bouclier énergétique |
| macron | Environ 1,2 million d'emplois ont été créés en France pendant le quinquennat d'Emmanuel Macron. | exact | inverifiable | Vérification impossible pour le moment |
| macron | Le taux de chômage au sens du BIT est passé de 9,6 % à 7,4 % pendant le quinquennat d'Emmanuel Macron. | exact | exact | Taux de chômage passé de 9,6 % à 7,4 % entre début 2017 et fin 2021 |
| macron | Les eurodéputés du Rassemblement national ont voté contre l'aide financière de l'UE à l'Ukraine. | plutot_exact | inverifiable | Aucune source exploitable sur les votes FN/RN concernant l'aide financière à l'U |
| macron | Le parti de Marine Le Pen a emprunté auprès d'une banque russe proche du pouvoir, la First Czech-Russian Bank. | plutot_exact | imprecis | Un prêt russe d'environ 9-10 millions d'euros contracté via la FCRB, mais en 201 |
| macron | Marine Le Pen a été l'une des premières responsables politiques à reconnaître le rattachement de la Crimée à l | plutot_exact | plutot_exact | Marine Le Pen a reconnu dès 2014 le référendum de rattachement de la Crimée à la |
| macron | Environ 80 % de la hausse des coups et blessures volontaires constatés depuis 2017 est due aux violences intra | exact | inverifiable | Part des violences intrafamiliales dans la hausse des violences aux personnes :  |
| macron | Le quinquennat d'Emmanuel Macron a créé 10 000 postes de policiers et gendarmes supplémentaires présents sur l | faux | exact | 10 000 postes de policiers et gendarmes créés sur le quinquennat |
| macron | Aucun pays n'interdit le voile islamique dans l'espace public comme le propose Marine Le Pen. | exact | inverifiable | Interdiction du voile dans l'espace public : pas de sources exploitables obtenue |
| macron | Les émissions de gaz à effet de serre de la France ont baissé deux fois plus vite sous Emmanuel Macron que sou | plutot_exact | imprecis | Émissions de GES : environ -9 % sur 2017-2021 contre -5 % sous le quinquennat pr |
| macron | Marine Le Pen a tenu des positions climatosceptiques. | plutot_exact | non détectée |  |
| macron | Le pouvoir d'achat des Français a progressé pendant le quinquennat d'Emmanuel Macron. | plutot_exact | plutot_exact | Pouvoir d'achat en hausse sous le quinquennat, environ +8 % selon le Trésor |
| macron | Il y a environ 50 000 travailleurs détachés en France. | faux | inverifiable | Aucune source exploitable obtenue sur le nombre de détachements et de travailleu |
| macron | Sous le quinquennat d'Emmanuel Macron, la France a créé plus d'emplois industriels qu'elle n'en a détruit. | imprecis | faux | Emploi industriel : environ -16 000 postes entre début 2017 et fin 2021, pas +10 |
| macron | Sur la hausse de 600 milliards de dette publique sous le quinquennat, seuls environ 200 milliards relèvent de  | faux | faux | Sur ~640 Md€ de dette publique en plus, l'État en représente ~530 Md€, pas 200 |
| macron | Le Conseil d'orientation des retraites (COR) prévoit un déséquilibre du système de retraite jusqu'au milieu de | trompeur | plutot_exact | Le COR table sur un déficit du régime des retraites jusqu'au milieu des années 2 |
| macron | Le budget de la justice a augmenté de 30 % sous le quinquennat d'Emmanuel Macron. | imprecis | inverifiable | Hausse de 30% des moyens de la justice 2020-2022 : aucune donnée sourcée obtenue |
| macron | Depuis 2019, aucune classe ne ferme en milieu rural sans l'accord du maire. | faux | inverifiable | Aucune source trouvée sur une règle imposant l'accord du maire pour fermer une c |
| macron | Le nombre de nouveaux apprentis par an est passé de moins de 300 000 à plus de 710 000 sous le quinquennat d'E | exact | non détectée |  |
| macron | Sur 99 mosquées soupçonnées de séparatisme, 23 ont été fermées sous le quinquennat d'Emmanuel Macron. | exact | non détectée |  |
| macron | La centrale nucléaire de Fessenheim n'avait plus fait l'objet d'aucun investissement depuis 2012. | faux | imprecis | Fessenheim : diesels post-Fukushima jamais bâtis, mais travaux de sûreté exigés  |
| macron | Emmanuel Macron a mis fin au numerus clausus des études de médecine. | plutot_exact | exact | Le numerus clausus a bien été supprimé par la loi de juillet 2019, effective à l |
| macron | Emmanuel Macron n'a pas pu instaurer la proportionnelle aux législatives faute d'accord sur sa réforme constit | trompeur | exact | Aucune révision constitutionnelle adoptée sous le premier quinquennat Macron |
| macron | Marine Le Pen n'a pas voté la déconjugalisation de l'AAH car elle était absente lors des votes à l'Assemblée. | exact | non détectée |  |
