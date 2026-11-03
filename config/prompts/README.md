Prompts d'extraction externalisés (VLM-T-005), versionnés avec le code. Le
nom et l'empreinte SHA-256 du prompt utilisé sont enregistrés dans chaque
dossier.

Origine : `build_eval_prompt` du POC (schéma v2), modifié selon SPEC §5 :
- supprimés : règle « all-caps → sentence case », casse des noms de
  personnes, retrait des civilités (relèvent du module NOR, VLM-T-003) ;
- ajoutés : « Transcribe every value exactly as printed (case, accents,
  civilities). », champ `abstract` (VLM-T-002, ING-T-002), mention de
  plusieurs images d'un même document (VLM-T-001).
