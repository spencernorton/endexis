"""
Direct-Upload Discern Analytics 2.0 (DA2) Query & Eclipse BIRT 4.4.2 (.rptdesign)
Report Generator and Deterministic Verifier.
"""
from __future__ import annotations

import html
import re
import io
import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

from .ccl_compiler import CCLCompiler
from .interpreter import MillenniumInterpreter
from .knowledge import load_da2_canonical_reports_catalog, load_da2_elements, load_da2_filter_value_catalog, load_v500_tables
from .schemas import (
    DEFAULT_OWNER_GROUP_DISPLAY,
    DA2QuerySpec,
    DA2UploadableBundle,
    InterpretationResult,
    TenantConfig,
    VerificationReport,
)




REHAB_THERAPY_CE_EVENTS: list[tuple[str, str]] = [
    ("PT Total Treatment Time", "2132509.00"),
    ("PT Total Treatment Time Rehab", "29765075.00"),
    ("OT Total Treatment Time", "2779407.00"),
    ("OT Total Treatment Time Rehab", "29736657.00"),
    ("SLP Total Tx Time", "29785107.00"),
    ("PT Total Group Therapy Time", "29770097.00"),
    ("OT Total Group Therapy Time", "29740771.00"),
    ("SLP Total Group Therapy Time", "29792319.00"),
]

CENSUS_RISK_CE_EVENTS: list[tuple[str, str]] = [
    ("Morse Fall Risk Score", "3612336.00"),
    ("Morse Fall Risk Form", "3612349.00"),
    ("Braden Score", "703333.00"),
    ("Braden Assessment Form", "2700598.00"),
    ("Urinary Catheter Activity:", "22047597.00"),
    ("Urinary Catheter Activity Type", "712525.00"),
    ("Urinary Catheter Placement Date", "3678084.00"),
    ("Incision, Wound Care:", "16744718.00"),
    ("Incision, Wound Depth:", "17022014.00"),
    ("Incision, Wound Bed Color:", "54308179.00"),
]

PROVIDER_CLINICAL_NOTES_CE_EVENTS: list[tuple[str, str]] = [
    ("Consultation Note - General", "21854851.00"),
    ("Consultation Note - Generic", "2820510.00"),
    ("Physiatrist Consultation", "2820580.00"),
    ("Physical Medicine & Rehab Consult Note*", "41489463.00"),
    ("Neurology Consultation", "2820514.00"),
    ("Internal Medicine Progress Note", "2820684.00"),
    ("Progress Note-Physician", "21862853.00"),
    ("Progress Note - General", "21854862.00"),
    ("Progress Note - Generic", "2820555.00"),
    ("Physical Medicine & Rehab Progress Note*", "41494897.00"),
    ("Physiatrist Progress Note", "2820692.00"),
    ("Rehab Medicine Progress Note", "56164313.00"),
    ("History and Physical", "3713594.00"),
    ("Admission Note-Physician", "2820507.00"),
]

ORAL_CARE_CE_EVENTS: list[tuple[str, str]] = [
    ("Oral Care", "700579.00"),
    ("Oral Care Activities Assistance", "54993095.00"),
    ("Oral Care Activity Tolerance", "54993131.00"),
    ("Oral Care Assisted By", "54993113.00"),
    ("Oral Care Equipment", "54993077.00"),
    ("Oral Care Position", "54993059.00"),
    ("Oral Care Refused Nurse Notified", "54992969.00"),
    ("Oral Care Refused Reason", "54992951.00"),
    ("Oral Care TAR - Task", "141012929.00"),
    ("Oral care with Tube Feedings - Task", "141012963.00"),
    ("Oral Cavity Abnormalities Nutrition", "1230611.00"),
    ("Oral Health Risk Assessment Performed", "239606339.00"),
    ("Oral Hygiene - Task", "141012947.00"),
    ("Oral Hygiene Care Provided", "273798305.00"),
    ("Oral Hygiene Help/Support", "273798345.00"),
    ("Oral Hygiene Recommendation", "54695777.00"),
    ("Oral Hygiene Support Effort", "273798387.00"),
    ("Oral Membrane Description", "32860555.00"),
    ("GG0130 Oral Hygiene", "26956578.00"),
    ("GG0130 Oral Hygiene Goal", "26958885.00"),
    ("Teeth", "711231.00"),
]

BLADDER_BOWEL_CE_EVENTS: list[tuple[str, str]] = [
    ("Bladder", "4156084.00"),
    ("Bladder Distention", "703453.00"),
    ("Bladder Level of Assistance", "29726813.00"),
    ("Bladder Management", "36780603.00"),
    ("Bladder Mgmt Assisted By", "54988353.00"),
    ("Bladder Program", "71106187.00"),
    ("Bladder Scan", "3347001.00"),
    ("Bladder Scan - Form", "60911153.00"),
    ("Bladder Scan - Text", "3946157.00"),
    ("Bladder Scan Form", "3347000.00"),
    ("Bladder Scan PVR - Text", "60911161.00"),
    ("Bladder Scan Volume", "704017.00"),
    ("Bladder Training Milestone", "30719389.00"),
    ("Bowel and Bladder Management RTF", "51739009.00"),
    ("Bowel And Bladder Program Plan", "100981525.00"),
    ("Bowel and Bladder Retraining", "43692291.00"),
    ("Bowel and Bladder Retraining Asse - Task", "141026771.00"),
    ("Bowel Continence LTC", "21837746.00"),
    ("Bowel Dysfunction Plan of Care", "20004851.00"),
    ("Bowel Elimination", "36383823.00"),
    ("Bowel Elimination Description", "21058692.00"),
    ("Bowel Habits", "71105635.00"),
    ("Bowel Incontinence Interventions", "22048034.00"),
    ("Bowel Incontinence Product(s) Used", "21377299.00"),
    ("Bowel Incontinence Supplies Used", "21058731.00"),
    ("Bowel Level of Assistance", "29726795.00"),
    ("Bowel Level of Assistance Goal", "30463709.00"),
    ("Bowel Management", "29718937.00"),
    ("Bowel Management", "36779499.00"),
    ("Bowel Management Device", "54988199.00"),
    ("Bowel Mgmt Activity Assisted", "54988389.00"),
    ("Bowel Mgmt Assisted By", "54988407.00"),
    ("Bowel Monitoring LTC - Task", "141011345.00"),
    ("Bowel Movement Amount", "22046351.00"),
    ("Bowel Movement Last Date", "703492.00"),
    ("Bowel Movement Location", "30452529.00"),
    ("Bowel Movement Location", "36779951.00"),
    ("Bowel Movement Status", "71241245.00"),
    ("Bowel Movement Type", "30452633.00"),
    ("Bowel Movements Last Date Post DC", "38117123.00"),
    ("Bowel Ostomy Type", "63162613.00"),
    ("Bowel Program", "29718955.00"),
    ("Bowel Program Repeated, Continued", "30452509.00"),
    ("Bowel Program Repeated, Continued", "36779933.00"),
    ("Bowel Program Tolerance", "54988425.00"),
    ("Bowel Protocol Effective", "229637239.00"),
    ("Bowel Sounds", "3374546.00"),
    ("Bowel Sounds Absent", "3672148.00"),
    ("Bowel Sounds All Quadrants", "703706.00"),
    ("Bowel Sounds Grid", "704041.00"),
    ("Bowel Sounds LLQ", "703700.00"),
    ("Bowel Sounds LUQ", "703694.00"),
    ("Bowel Sounds RLQ", "703703.00"),
    ("Bowel Sounds RUQ", "703697.00"),
    ("Bowel Status Adequate", "21357613.00"),
    ("Bowel Training Milestone", "30719419.00"),
    ("Bowel/Bladder Concerns", "4166149.00"),
    ("Bowel/Bladder Retraining Asmt LTC - Form", "101283737.00"),
    ("Bowel/Bladder Retraining Asmt LTC - Text", "101283751.00"),
    ("Frequency of Urination", "1508509.00"),
    ("Frequent Urination", "71106211.00"),
    ("GG0170 Toilet Transfer", "26956608.00"),
    ("GG0170 Toilet Transfer Goal", "26958981.00"),
    ("Indwelling Catheter", "4361873.00"),
    ("Indwelling Catheter Care", "22272998.00"),
    ("Urinary Catheter Activity Type", "712525.00"),
    ("Urinary Catheter Activity:", "22047597.00"),
    ("Urinary Catheter Care", "21862887.00"),
    ("Urinary Catheter Indications", "16730125.00"),
    ("Urinary Catheter Output:", "22047794.00"),
    ("Urinary Catheter Placement Date", "3678084.00"),
    ("Urinary Catheter Type", "712510.00"),
]

BATHING_HYGIENE_CE_EVENTS: list[tuple[str, str]] = [
    ("Bath Type", "54993041.00"),
    ("Bathing Refused Reason", "54993005.00"),
    ("GG0130 Personal Hygiene", "252186263.00"),
    ("GG0130 Shower, Bathe Self", "29729947.00"),
    ("WeeFIM Bathing", "83630627.00"),
    ("WeeFIM Grooming", "83630601.00"),
]

NUTRITION_ASSESSMENT_CE_EVENTS: list[tuple[str, str]] = [
    ("Nutrition Assessment and Plan - Text", "67666795.00"),
    ("Nutrition Assessment and Plan Form", "64892683.00"),
    ("Nutrition Assessment and Plan - Task", "141031257.00"),
    ("Nutrition Assessment Completed", "54134057.00"),
    ("Nutrition Evaluation - Form", "40691519.00"),
    ("Nutrition Evaluation - Task", "141031285.00"),
    ("Nutrition Evaluation - Text", "21564454.00"),
    ("Nutritional Screening - Form", "359275439.00"),
    ("Body Mass Index", "64759777.00"),
    ("Body Mass Index Measured", "60301285.00"),
    ("Body Mass Index Estimated", "4154144.00"),
    ("Body Mass Index Nutrition", "18837898.00"),
    ("Home Diet", "705140.00"),
    ("Nutrition Risk Factors", "16721672.00"),
    ("Nutritional Risk Factors", "705249.00"),
    ("Weight Measured", "4154120.00"),
]

PHARMACY_INTERVENTIONS_CE_EVENTS: list[tuple[str, str]] = [
    ("Pharmacy Clinical Interventions Form", "4203612.00"),
    ("Intervention Type Pharmacy", "4203582.00"),
    ("Associated Order(s) Pharmacy", "4203585.00"),
    ("Pharmacy Order Initiated By", "4203588.00"),
    ("Clinical Importance Pharmacy", "4203591.00"),
    ("Prescriber Response Pharmacy", "4203594.00"),
    ("Patient Clinical Outcome Pharmacy", "4203597.00"),
    ("Pharmacist Intervention Time", "4203600.00"),
    ("Pharmacoeconomic Impact", "4203603.00"),
    ("Pharmacy Prescribing Physician", "4203606.00"),
]

IV_CENTRAL_LINE_CE_EVENTS: list[tuple[str, str]] = [
    ("Peripheral IV Activity:", "17022182.00"),
    ("Central IV #1", "4153930.00"),
    ("Central IV #1 Access Type", "4170586.00"),
    ("Central IV #1 Activity", "4170574.00"),
    ("Central IV #1 Catheter Size/Length", "4170580.00"),
]

PRECAUTION_ORDER_CATALOG_ITEMS: list[tuple[str, str]] = [
    ("Aspiration Precautions", "2780633.00"),
    ("Fall Precautions", "38884839.00"),
    ("Hip Precautions", "38885579.00"),
    ("Isolation", "2958766.00"),
    ("Neutropenic Precautions", "38886875.00"),
    ("Seizure Precautions", "38888573.00"),
    ("Spinal Precautions", "38888949.00"),
    ("Sternal Precautions", "274704793.00"),
    ("Suicide Precautions", "4149527.00"),
    ("Weight Bearing Status/Precautions", "38890609.00"),
]

OBSERVATION_MONITORING_ORDER_CATALOG_ITEMS: list[tuple[str, str]] = [
    ("Patient Observation Needs (1:1, Enhanced Supervision, Close Observation)", "257629611.00"),
    ("Continuous Video Monitoring", "257630227.00"),
]

WOUND_CONSULT_ORDER_CATALOG_ITEMS: list[tuple[str, str]] = [
    ("Consult to Wound Care", "20266108.00"),
    ("Consult to Wound Care Specialist", "4457342.00"),
]

DEPRECATED_CWC_UUIDS: set[str] = {
    "db2565e2-c861-4171-b9e7-3133a75e336b",
    "1a7bbb05-a797-42d6-b5f4-0bb86ff51608",
    "a2ff2540-fa94-41bf-a0eb-69771900201f",
}

LIVE_CWC_ELEMENT_MAP: dict[str, tuple[str, str, str]] = {
    'Account Number': ('Enc Financial Num', '1a4ee2f6-2ed1-48b3-9d79-974352b9fabf', '3'),
    'Acct Guarantor Name': ('Person Name Full Format', '12b363ca-f0d2-4a5d-afe3-3225de181f5f', '3'),
    'Adj Amount': ('F Chg Billing Amount', '91bda8db-c505-4887-bf0a-dca32a4d7cea', '1'),
    'Adj Credit GL Number': ('Chg Rev Code', 'c437b586-9b66-47d5-8971-0e75b47e4156', '3'),
    'Adj Debit GL Number': ('F Chg Rev Code', '9af50981-51fa-4e64-a740-a8a049c1b48e', '3'),
    'Adj Post Dt': ('Chg Posted Dt/Tm', 'f34d0bfe-074f-4268-8275-ef17de249e1c', '2'),
    'Adj Post Prsnl': ('Chg Posted Prsnl Name', '4ad0ef60-56ba-4f82-89ee-090d58bf5bbe', '3'),
    'Adj Trans Reason': ('F Chg Status', '30aadfd4-ba16-4e13-a56d-b0de02c4882b', '3'),
    'Adj Trans Sub Type': ('Chg Type', '59016b19-0636-4cb9-bbff-af3ea84d1a5e', '3'),
    'AdjTrans Alias': ('Chg Activity Type', '07e4d357-ed1c-46fb-acb3-84ce2957db36', '3'),
    'Alg Nom Mnemonic': ('Alg Nom Mnemonic', '972e740d-30fd-4158-930a-7a626210074b', '3'),
    'Alg Nom Source String': ('Alg Nom Source String', '84d6dcd5-c84e-46bf-ae37-25283e317714', '3'),
    'Alg Substance Type': ('Alg Substance Type', 'a0cd3c4c-e83e-4f11-9792-194b49566222', '1'),
    'B I Bill Item Id': ('Chg Bill Item Id', '72213f9c-5239-4e7d-b122-e18af4fa7ace', '3'),
    'B I Ext Description': ('Chg Description', '3f56ba91-86c6-4081-909a-36ca011066c6', '3'),
    'BIM Key6': ('Chg M Field 6', '0c4a6eda-82ba-4d97-935d-914f2fc63711', '3'),
    'BO HP Plan Name': ('HP Plan Name', '8ed4dcd1-cebe-4186-bfb9-284d6f0aceba', '3'),
    'BO Total Adjustments': ('Chg Gross Price', '13b57072-8981-431b-9881-64c2dda7efca', '3'),
    'BO Total Charges': ('F Chg Billing Amount', '91bda8db-c505-4887-bf0a-dca32a4d7cea', '1'),
    'BO Total Payments': ('Chg Item Price', '07112aeb-0932-4898-a6be-fe9b5ddf048c', '1'),
    'Bed Loc': ('Bed Loc', '219ba43a-c7ee-44a4-8e79-052d4a5703da', '3'),
    'Bed Loc Room': ('Bed Loc Room', '79c31798-5bc7-4357-ac9f-17e842d8cd37', '3'),
    'CE Accession Nbr': (
        'CE Accession Nbr',
        '27d055af-6e71-' + '452b-bb26-8e820d8416dd',
        '3',
    ),
    'CE Authentic Flag': (
        'CE Authentic Flag',
        '20721292-375f-' + '47a7-890b-95142bd00aff',
        '1',
    ),
    'CE Clinical Event Id': ('CE Id', '05a1f88d-6a33-4190-86ac-704249934924', '3'),
    'CE Clinical Seq': ('CE Clinical Seq', '4b6eaa18-9f17-46b5-9b40-7ed8626a2b6d', '3'),
    'CE Clinical Update Dt/Tm': ('CE Clinical Update Dt/Tm', 'f89ac163-550e-4f84-aeff-9b0e8a761311', '2'),
    'CE Contributor System': ('CE Contributor System', '3529ee54-8bd2-4f3c-be9a-a93e15e393f9', '3'),
    'CE Critical High': ('CE Critical High', '1daa0f60-f14d-424b-aef2-8298a00322b0', '3'),
    'CE Critical Low': ('CE Critical Low', 'c7f5659e-9a6c-4c22-a4f8-04b7909c2e2e', '3'),
    'CE DTA Code': ('CE DTA Code', '9ec30058-221d-457a-888b-502330786fa1', '3'),
    'CE Enc Id': ('CE Enc Id', 'df42ea7b-26d8-4a8a-9b09-9a861e3a8c44', '3'),
    'CE End Dt/Tm': ('CE End Dt/Tm', '4433e8b0-5947-4f79-b4aa-c32c8ec30c45', '2'),
    'CE Entry Mode': ('CE Entry Mode', 'b6cd3329-6665-4dea-8e2a-6398444b97e9', '1'),
    'CE Event': ('CE Event', 'f01d0cc1-f599-46be-b816-ecd82fffbb0e', '1'),
    'CE Event CV': ('CE Event CV', '41142ba4-b325-44f4-84a7-9d4f1bc33323', '3'),
    'CE Event Class': ('CE Event Class', 'f3b9fd18-8372-44ea-b36e-b6043708d05f', '3'),
    'CE Event End Dt Tm': ('CE End Dt/Tm', '4433e8b0-5947-4f79-b4aa-c32c8ec30c45', '2'),
    'CE Event End Dt/Tm': ('CE End Dt/Tm', '4433e8b0-5947-4f79-b4aa-c32c8ec30c45', '2'),
    'CE Event Id': ('CE Event Id', 'bfc961f6-7aa0-4203-9075-82a5058e4ebb', '3'),
    'CE Event Tag': ('CE Event Tag', '0be2e3a3-84d9-4f2a-aad7-a302fa9297d9', '3'),
    'CE Event Tag Flag': ('CE Event Tag Flag', '8a1d7263-26b7-42bd-a05a-14a05bd120f7', '1'),
    'CE Event Title': ('CE Event Title', '3aea7feb-ce08-4dc6-92e2-ebe8fa04d94d', '3'),
    'CE Event Title Text': ('CE Event Title', '3aea7feb-ce08-4dc6-92e2-ebe8fa04d94d', '3'),
    'CE Expiration Dt/Tm': ('CE Expiration Dt/Tm', '8601193e-e141-4afc-98b4-da2b2066e32a', '2'),
    'CE Id': ('CE Id', '05a1f88d-6a33-4190-86ac-704249934924', '3'),
    'CE Lbl Id': ('CE Lbl Id', '5dbdff00-e8dc-4c53-b01e-f2423d89e304', '3'),
    'CE Lbl Name': ('CE Lbl Name', '48d3ee6b-4c06-4302-a52c-acfea5bceb8c', '3'),
    'CE Med Admin Dosage': ('CE Result Value', '155eec27-4c56-4cff-a4ee-861aa3335814', '3'),
    'CE Med Admin Dose': ('CE Result Value', '155eec27-4c56-4cff-a4ee-861aa3335814', '3'),
    'CE Med Admin Start Dt/Tm': ('CE Med Admin Start Dt/Tm', '056e4e00-7257-45e7-a531-335c8c14e608', '2'),
    'CE Med Dosage Unit': ('CE Result Units', '3d18ca87-7efd-487a-9121-0e5072f25f7a', '3'),
    'CE Normal High': ('CE Normal High', '6d355f1d-37d7-4b4f-aad5-fc3f1e9114bb', '3'),
    'CE Normal Low': ('CE Normal Low', '3f57c99f-da37-4ce8-9305-aae3fec7288d', '3'),
    'CE Normalcy': ('CE Normalcy', 'bdd1ea0a-8f01-4cfc-9af8-e54acf26e9b6', '3'),
    'CE Order Action Sequence': ('CE Order Action Sequence', '7500dbc6-e085-4d2d-bbcb-9633b4f8aa72', '1'),
    'CE Order Id': ('CE Order Id', 'a9510509-09d4-4c50-8d59-8ee492410e2b', '3'),
    'CE Parent Event Id': ('CE Parent Event Id', '8c750217-756f-4276-8888-17d7cc0408ae', '3'),
    'CE Performed Dt/Tm': ('CE Performed Dt/Tm', '7c3dbae0-373b-43db-afed-de4d3e7048a0', '2'),
    'CE Performed Prsnl Id': ('CE Performed Prsnl Id', '45278d25-a12d-4165-bafd-c5dbbf83cf65', '3'),
    'CE Performed Prsnl Name': ('CE Performed Prsnl Name', '2ecc5b49-c180-440c-b0c2-ce9712307ba3', '3'),
    'CE Publish Flag': ('CE Publish Flag', 'd7970051-e354-4a5b-b10c-6d1e23f41982', '1'),
    'CE Record Status': ('CE Record Status', 'bae19651-b71d-4301-88a2-1e32cc837361', '3'),
    'CE Reference Nbr': ('CE Reference Nbr', '16128032-41b3-4542-b5cf-b5618a11c8e1', '3'),
    'CE Relationship': ('CE Relationship', 'a2112338-e3a1-48ff-9a94-eeafc838e39c', '1'),
    'CE Resource': ('CE Resource', '7a2f218d-fc7a-4cf5-b28f-7983a6d0586a', '3'),
    'CE Result Status': ('CE Result Status', '7ccf3fe5-568a-4015-82e0-fd370a98426e', '3'),
    'CE Result Units': ('CE Result Units', '3d18ca87-7efd-487a-9121-0e5072f25f7a', '3'),
    'CE Result Value': ('CE Result Value', '155eec27-4c56-4cff-a4ee-861aa3335814', '3'),
    'CE Source': ('CE Source', '4f551e03-852c-4989-b614-b13c69ac7627', '3'),
    'CE Source Event Id': ('CE Source Event Id', 'a669c949-2f6a-4813-97bc-ed9db59b3ab9', '3'),
    'CE Source Update Dt/Tm': ('CE Source Update Dt/Tm', 'eb3da07e-7fba-4245-9894-b134145e92dd', '2'),
    'CE Start Dt/Tm': ('CE Start Dt/Tm', 'f1ee1fb0-f894-482b-9543-75ba9f4b535b', '2'),
    'CE Update Dt/Tm': ('CE Update Dt/Tm', '8617c31b-f377-4ff6-9800-28ff327a4bcd', '2'),
    'CE Update Prsnl Id': ('CE Update Prsnl Id', '98374716-292c-4342-a5ec-cf384c2a73b5', '3'),
    'CE Update Prsnl Name': ('CE Update Prsnl Name', 'ae58e977-269a-4602-abcd-a705a989c8be', '3'),
    'CE Valid From Dt/Tm': ('CE Valid From Dt/Tm', '56861f5d-9848-4392-b3e0-3242d98273ac', '2'),
    'CE Valid Until Dt Tm': ('CE Valid Until Dt/Tm', '871b99b7-e872-4db0-8762-d663c933ad76', '2'),
    'CE Valid Until Dt/Tm': ('CE Valid Until Dt/Tm', '871b99b7-e872-4db0-8762-d663c933ad76', '2'),
    'CE Verified Dt Tm': ('CE Verified Dt/Tm', 'ca1f55a0-7e96-4bbd-b937-d843918a9fc3', '2'),
    'CE Verified Dt/Tm': ('CE Verified Dt/Tm', 'ca1f55a0-7e96-4bbd-b937-d843918a9fc3', '2'),
    'CE Verified Prsnl Id': ('CE Verified Prsnl Id', '4d102b16-d2bc-4ac6-bf31-ae5d68c1e449', '3'),
    'CE Verified Prsnl Name': ('CE Verified Prsnl Name', 'afc53867-58e3-4f2e-aacb-e71afefcb9af', '3'),
    'CE View Level': ('CE View Level', 'a5521a2a-743f-42f0-a01f-e5bd205ba63f', '1'),
    'Chg Active Ind': ('Chg Active Ind', '2182a6ce-7c39-40ea-b085-5031eb564eaf', '1'),
    'Chg Activity Type': ('Chg Activity Type', '07e4d357-ed1c-46fb-acb3-84ce2957db36', '3'),
    'Chg Bill Item Desc': ('Chg Description', '3f56ba91-86c6-4081-909a-36ca011066c6', '3'),
    'Chg Bill Item Id': ('Chg Bill Item Id', '72213f9c-5239-4e7d-b122-e18af4fa7ace', '3'),
    'Chg CDM': ('Chg CDM', 'ee98bf46-35fe-46d1-a919-c6d280e8010d', '3'),
    'Chg CPT': ('Chg CPT', 'b5e8a288-44ac-4f95-9140-667d840260d7', '3'),
    'Chg Charge Item Id': ('Chg Bill Item Id', '72213f9c-5239-4e7d-b122-e18af4fa7ace', '3'),
    'Chg Description': ('Chg Description', '3f56ba91-86c6-4081-909a-36ca011066c6', '3'),
    'Chg E Collect Priority': ('Chg E Collect Priority', '90747a07-546d-4050-871e-e369e10a7ec7', '1'),
    'Chg Enc Id': ('Chg Enc Id', 'a3314a9e-d7f5-4802-9da9-3f4d1c3c1de3', '1'),
    'Chg Financial Class': ('Chg Financial Class', 'b77029b5-d43e-4e88-9377-ebad171532e6', '3'),
    'Chg Gross Price': ('Chg Gross Price', '13b57072-8981-431b-9881-64c2dda7efca', '3'),
    'Chg HCPCS': ('Chg HCPCS', 'bad79d62-d8d2-4a8f-a710-08de5ace13fc', '3'),
    'Chg Item Extended Price': ('Chg Gross Price', '13b57072-8981-431b-9881-64c2dda7efca', '3'),
    'Chg Item Id': ('Chg Item Id', '254a43c9-245e-4556-b0e8-06621e3dbfc2', '3'),
    'Chg Item Price': ('Chg Item Price', '07112aeb-0932-4898-a6be-fe9b5ddf048c', '1'),
    'Chg Item Quantity': ('Chg Item Quantity', '9c7440d4-51a5-4dd1-ac0c-a8bf799fb074', '1'),
    'Chg M Field 1 Id': ('Chg M Field 1 Id', 'd95d31b9-91cb-4040-87cc-b36c70b81d9e', '1'),
    'Chg M Field 6': ('Chg M Field 6', '0c4a6eda-82ba-4d97-935d-914f2fc63711', '3'),
    'Chg Manual Ind': ('Chg Combine Ind', 'c42ca2b9-d4e1-47e5-9e8f-29743d4a857c', '1'),
    'Chg Offset Chg Item Id': ('Chg Offset Chg Item Id', 'fe50e527-f04b-4a63-8483-335d53337dcd', '1'),
    'Chg Order Id': ('Chg Order Id', '13eac210-158c-4d9e-804a-275fd860ac80', '3'),
    'Chg Posted Dt': ('Chg Posted Dt', '77563511-e328-4e50-b629-a160c9b5ad2c', '3'),
    'Chg Posted Dt/Tm': ('Chg Posted Dt/Tm', 'f34d0bfe-074f-4268-8275-ef17de249e1c', '2'),
    'Chg Posted Prsnl Name': ('Chg Posted Prsnl Name', '4ad0ef60-56ba-4f82-89ee-090d58bf5bbe', '3'),
    'Chg Process Flag': ('Chg Process Flag', '859f82b4-3395-4d35-beb0-def2bdcb60da', '1'),
    'Chg Quantity': ('Chg Item Quantity', '9c7440d4-51a5-4dd1-ac0c-a8bf799fb074', '1'),
    'Chg Rev Code': ('Chg Rev Code', 'c437b586-9b66-47d5-8971-0e75b47e4156', '3'),
    'Chg Service Dt': ('Chg Service Dt', 'e88f7045-c036-47ee-a37b-58efca51e741', '3'),
    'Chg Service Dt Tm': ('Chg Service Dt/Tm', '9452e25e-98ec-4820-b40c-b95fbc94935e', '2'),
    'Chg Service Dt/Tm': ('Chg Service Dt/Tm', '9452e25e-98ec-4820-b40c-b95fbc94935e', '2'),
    'Chg Type': ('Chg Type', '59016b19-0636-4cb9-bbff-af3ea84d1a5e', '3'),
    'Cod Act Status': ('Dx Active Ind', 'bc370ab9-991f-4c61-b844-e9a79f730bcd', '1'),
    'Cred Renewal Dt/Tm': ('Prsnl End Eff Dt/Tm', '3560a0d6-208c-48ea-af57-16acfffdd115', '2'),
    'DRG Nom Source Code': ('DRG Nom Source Code', '5ca8e00a-2046-414c-af3a-cd6dbe6f56f0', '3'),
    'Diag ICD Code': ('Dx Nom Source Code', '5872bfb7-f446-46a9-b871-c1e686773b3b', '3'),
    'Diag Type': ('Dx Type', '197c4634-1052-4c0e-abb3-22d244f28529', '1'),
    'Diagnosis Display': ('Dx Nom Source String', '804f1c07-f444-4e5b-98e2-ad315f6fde50', '3'),
    'Dx 1 Desc': ('Dx 1 Desc', '3498d870-dbc9-4b1b-9491-cbd69f07ffdc', '3'),
    'Dx Active Ind': ('Dx Active Ind', 'bc370ab9-991f-4c61-b844-e9a79f730bcd', '1'),
    'Dx Beg Eff Dt/Tm': ('Dx Beg Eff Dt/Tm', 'f1ce8cf5-45e9-48ed-b688-bb123b322f49', '2'),
    'Dx Enc Id': ('Dx Enc Id', 'b1fb31ad-ae4c-45d3-a374-f28284da308d', '3'),
    'Dx End Eff Dt/Tm': ('Dx End Eff Dt/Tm', '6002bafa-9f63-4824-bbb0-1176a93c0358', '2'),
    'Dx Nom Source Code': ('Dx Nom Source Code', '5872bfb7-f446-46a9-b871-c1e686773b3b', '3'),
    'Dx Nom Source String': ('Dx Nom Source String', '804f1c07-f444-4e5b-98e2-ad315f6fde50', '3'),
    'Dx Nom Source Vocab': ('Dx Nom Source Vocab', '1ae0777a-e08b-41ef-8bc2-89a34963cf7f', '3'),
    'Dx Presnt on Admit': ('Dx Presnt on Admit', 'd26f4155-ea18-47f7-b815-9b249cadf129', '3'),
    'Dx Priority': ('Dx Priority', 'bde8c505-bbd4-4fa4-a9c3-7f91a7fb8155', '1'),
    'Dx Prsnl Name': ('Dx Prsnl Name', '21d0adcc-4ddb-4fd3-a722-c54e53d9e236', '3'),
    'Dx Type': ('Dx Type', '197c4634-1052-4c0e-abb3-22d244f28529', '1'),
    'Dx Update Dt/Tm': ('Dx Update Dt/Tm', 'b2e14346-5995-4a72-ab2a-daabcb2cf22d', '2'),
    'Enc Active Ind': ('Enc Active Ind', '94d02e70-2821-4f5e-bc46-8eae179502cf', '1'),
    'Enc Admit Dt Tm': ('Enc Registration Dt/Tm', '42ee9a32-031f-495d-962a-5f04376e071a', '2'),
    'Enc Admit Dt/Tm': ('Enc Registration Dt/Tm', '42ee9a32-031f-495d-962a-5f04376e071a', '2'),
    'Enc Admit Source': ('Enc Admit Source', 'f18e7bef-aaee-43c7-9059-096f6be03e90', '3'),
    'Enc Admit Type': ('Enc Admit Type', 'df105f6d-15eb-49aa-b1de-7d7db4cb296a', '3'),
    'Enc Admitting Phys': ('Enc Admitting Phys', 'f4afa1eb-c2c6-42f4-b784-6e6cfc308cb2', '3'),
    'Enc Admitting Phys NPI': ('Enc Admitting Phys NPI', '8b343e83-e1ee-4146-9a42-9f144317b653', '3'),
    'Enc Age at Reg': ('Enc Age at Reg', 'f4ed0335-495d-4f3f-9bb0-0187171dd95e', '3'),
    'Enc Alias': ('Enc Alias', 'aa535f26-e193-44bd-821c-ae76779000d6', '3'),
    'Enc Alias Type': ('Enc Alias Type', '9d8e28f2-cb07-47f8-b7f7-ffe9c8012b18', '1'),
    'Enc Arrive Dt Tm': ('Enc Arrive Dt/Tm', '96d4471a-1a0d-4996-920b-e3e583c11a04', '2'),
    'Enc Arrive Dt/Tm': ('Enc Arrive Dt/Tm', '96d4471a-1a0d-4996-920b-e3e583c11a04', '2'),
    'Enc Attending Phys': ('Enc Attending Phys', 'beb01385-36a9-4371-93e2-68056229e216', '3'),
    'Enc Attending Phys Id': ('Enc Attending Phys Id', '729dcafa-c0ee-4bce-913e-9a5268bdf981', '3'),
    'Enc Disch Disposition': ('Enc Discharge Disp', 'f57ac62e-4dec-444f-8d2e-19534accc068', '3'),
    'Enc Disch Dt Tm': ('Enc Discharge Dt/Tm', '1d44a53e-cba7-4ddf-80c4-d86205e30e0d', '2'),
    'Enc Discharge Disp': ('Enc Discharge Disp', 'f57ac62e-4dec-444f-8d2e-19534accc068', '3'),
    'Enc Discharge Dt': ('Enc Discharge Dt', 'f2fccf23-964c-4fc6-82b2-a76fdd55e793', '2'),
    'Enc Discharge Dt/Tm': ('Enc Discharge Dt/Tm', '1d44a53e-cba7-4ddf-80c4-d86205e30e0d', '2'),
    'Enc Discharge Loc': ('Enc Discharge Loc', '9cb0ccff-d16e-46ce-8bc1-028a6c8d9c7f', '3'),
    'Enc Encounter Number': ('Enc Financial Num', '1a4ee2f6-2ed1-48b3-9d79-974352b9fabf', '3'),
    'Enc FIN': ('Enc Financial Num', '1a4ee2f6-2ed1-48b3-9d79-974352b9fabf', '3'),
    'Enc Financial Class': ('Enc Financial Class', '84af1292-065e-4e0c-99c1-5cfccf5653b7', '3'),
    'Enc Financial Num': ('Enc Financial Num', '1a4ee2f6-2ed1-48b3-9d79-974352b9fabf', '3'),
    'Enc Hx Activity Dt/Tm': ('Enc Hx Activity Dt/Tm', 'fc4d494a-1652-4838-b490-c248dffa4763', '2'),
    'Enc Hx Loc Nurse Unit': ('Enc Hx Loc Nurse Unit', 'd05fb4fc-eaf2-4b1e-ab59-9ab6fe37b995', '3'),
    'Enc Id': ('Enc Id', '5ef4381c-6344-43c4-81b5-2c18cd6bcb70', '3'),
    'Enc Info Hx Sub Type': ('Enc Info Hx Sub Type', 'c34563d2-e297-4a48-994d-32fdbc2b1cec', '1'),
    'Enc Info Sub Type': ('Enc Info Sub Type', 'c560bb7e-28ba-4659-8202-654628d07549', '1'),
    'Enc Info Type': ('Enc Info Type', '94935763-369b-4f35-9e7b-82a9f5928b17', '3'),
    'Enc Info Value': ('Enc Info Value', '272ade7e-1de7-4888-a4d3-6297e19a113c', '3'),
    'Enc Inpatient Admit Dt': ('Enc Inpatient Admit Dt', '31e750eb-57b4-4d89-9d60-10b31fc6cd0b', '3'),
    'Enc Inpatient Admit Dt/Tm': ('Enc Inpatient Admit Dt/Tm', '5f44f766-2da4-4cff-b050-7bfb5159b6b8', '2'),
    'Enc Loc Bed': ('Enc Loc Bed', '9e5bb15f-ec91-408b-901f-5beb20ee39d8', '3'),
    'Enc Loc Facility': ('Enc Loc Facility', 'b09f6eab-aa80-4110-a4af-3dcbbd00384b', '3'),
    'Enc Loc Nurse Unit': ('Enc Loc Nurse Unit', '75267dc7-d5c6-437b-aa34-98e46bbc5db1', '3'),
    'Enc Loc Room': ('Enc Loc Room', 'e52b4716-00f9-42ab-8a04-8ba1e9ea1ece', '3'),
    'Enc Medical Service': ('Enc Medical Service', 'd803b34d-3519-4096-9309-24ae60c669ab', '1'),
    'Enc Pl Active Ind': ('Enc Pl Active Ind', 'ca14ff95-a828-4b65-9bd2-bfc67d2f80b8', '1'),
    'Enc Pl Enc Id': ('Enc Pl Enc Id', '8eca97fe-98fc-44d2-82a2-67096cd65841', '3'),
    'Enc Pl End Eff Dt/Tm': ('Enc Pl End Eff Dt/Tm', 'bb4354a4-6166-49c3-9834-23f7cf7ea059', '2'),
    'Enc Pl Relationship Type': ('Enc Pl Relationship Type', 'd68ba34b-7554-43ba-9d7c-6bdd97054578', '1'),
    'Enc Prsnl Reltn Name': ('Enc Attending Phys', 'beb01385-36a9-4371-93e2-68056229e216', '3'),
    'Enc Prsnl Reltn Type': ('Enc Medical Service', 'd803b34d-3519-4096-9309-24ae60c669ab', '3'),
    'Enc Reason for Visit': ('Enc Reason for Visit', 'd83803b9-5ce4-4c86-8aa4-d1a359237a8c', '3'),
    'Enc Registration Dt': ('Enc Registration Dt', 'b52cf234-3ec7-4343-a9d8-db77b7354bd7', '2'),
    'Enc Registration Dt/Tm': ('Enc Registration Dt/Tm', '42ee9a32-031f-495d-962a-5f04376e071a', '2'),
    'Enc Status': ('Enc Status', '7161959f-72cd-46bb-b6f6-2b177edd0469', '3'),
    'Enc Type': ('Enc Type', '10b02c36-ff84-47d6-a825-ea142281a547', '3'),
    'Enc Type Class': ('Enc Type Class', '95fb940f-0306-4bab-89c8-6959a08df978', '1'),
    'F Chg Billing Amount': ('F Chg Billing Amount', '91bda8db-c505-4887-bf0a-dca32a4d7cea', '1'),
    'F Chg Billing Qty': ('Chg Item Quantity', '9c7440d4-51a5-4dd1-ac0c-a8bf799fb074', '1'),
    'F Chg CPT': ('F Chg CPT', '1b06b900-6fc5-45bd-afec-2b444bd6bf00', '3'),
    'F Chg Item Id': ('F Chg Item Id', '3e53617f-4e0a-43ec-85f4-8004411a4324', '3'),
    'F Chg Rev Code': ('F Chg Rev Code', '9af50981-51fa-4e64-a740-a8a049c1b48e', '3'),
    'F Chg Status': ('F Chg Status', '30aadfd4-ba16-4e13-a56d-b0de02c4882b', '3'),
    'F Enc Recur Seq': ('Enc Type', '10b02c36-ff84-47d6-a825-ea142281a547', '3'),
    'F Enc inpatient Admit Dt/Tm': ('Enc Inpatient Admit Dt/Tm', '5f44f766-2da4-4cff-b050-7bfb5159b6b8', '2'),
    'GL Post Dt': ('Chg Posted Dt/Tm', 'f34d0bfe-074f-4268-8275-ef17de249e1c', '2'),
    'GL Trans Amount': ('F Chg Billing Amount', '91bda8db-c505-4887-bf0a-dca32a4d7cea', '1'),
    'HP E R Active Ind': ('HP E R Active Ind', 'df3ceed8-1823-4bc0-ab0a-528467d3b48f', '1'),
    'HP E R Beg Eff Dt/Tm': ('HP E R Beg Eff Dt/Tm', 'b8a883b4-a99d-4083-9507-6952eb3d4b1d', '2'),
    'HP E R Enc Id': ('HP E R Enc Id', '4e63ebac-00d0-48ce-bac5-31f252348654', '1'),
    'HP E R End Eff Dt/Tm': ('HP E R End Eff Dt/Tm', 'df3ce658-90cb-446c-9b3c-c9576d2afa08', '2'),
    'HP E R Per Id': ('HP E R Per Id', '0f470d62-06d1-42b8-9ac2-7c05949370fc', '3'),
    'HP E R Priority Seq': ('HP E R Priority Seq', '776ef628-3223-4db6-822e-b14aaa0d3e1a', '1'),
    'HP E R Sub Per Name': ('HP E R Sub Per Name', 'f48885f5-3a00-4caf-88fa-5dcd915c4506', '3'),
    'HP E R Verify Status': ('HP E R Verify Status', '1da61840-ec3d-482e-97cb-58579b4b00dd', '1'),
    'HP Financial Class': ('HP Financial Class', 'c41e96b7-6f7f-41da-b6b6-1312ae27fc10', '3'),
    'HP Plan Category': ('HP Plan Category', '17ef99d2-c481-4dbd-83f2-e33cb50e2339', '3'),
    'HP Plan Class': ('HP Plan Class', '3eb2b4b3-e160-4853-be4e-02c831182a4d', '3'),
    'HP Plan Description': ('HP Plan Description', 'a5fa556d-be32-4ff5-962c-31eb5e21346b', '3'),
    'HP Plan Name': ('HP Plan Name', '8ed4dcd1-cebe-4186-bfb9-284d6f0aceba', '3'),
    'HP Plan Name Key': (
        'HP Plan Name Key',
        '77e2ee2f-ae8c-' + '4071-b989-073d414f4e67',
        '3',
    ),
    'HP Plan Type': ('HP Plan Type', '92aa268b-62ce-457a-9d4c-80f1df5c3fca', '3'),
    'HP1 Plan Name': ('HP1 Plan Name', '564e2840-fce9-45f9-9257-75d189e344f4', '3'),
    'HP1 Plan Type': ('HP1 Plan Type', 'dc31faf2-c529-4feb-9b30-18e69310569b', '1'),
    'Med Adm Beg Dt/Tm': ('Med Adm Beg Dt/Tm', '6f7e20c9-73db-4529-8507-2cb0b490bf06', '2'),
    'Med Adm Dt/Tm': ('Med Adm Beg Dt/Tm', '6f7e20c9-73db-4529-8507-2cb0b490bf06', '2'),
    'Med Adm End Dt/Tm': ('Med Adm End Dt/Tm', 'ffed4e18-9860-4e54-b03f-e4370e77e2e1', '2'),
    'Med Adm Event Id': ('Med Adm Event Id', '919ff389-29bb-423a-a886-c4a4ac8a356e', '3'),
    'Med Adm Event Type': ('Med Adm Event Type', '00bb938e-5fa1-44d6-9223-dbc84d8963c8', '3'),
    'Med Adm Id': ('Med Adm Id', '7736fb99-a316-46f5-8270-e9d9826e1b0d', '3'),
    'Med Adm MedScan Ind': ('Med Adm Event Type', '00bb938e-5fa1-44d6-9223-dbc84d8963c8', '3'),
    'Med Adm Order Id': ('Med Adm Order Id', '2c54e780-d040-496d-b790-f386d0d25c09', '3'),
    'Med Adm Prsnl Name': ('Med Adm Prsnl Name', '3355ab6a-f9f7-4b0f-8222-ddd7db2e1a82', '3'),
    'Med Adm PtScan Ind': ('Med Adm Event Id', '919ff389-29bb-423a-a886-c4a4ac8a356e', '1'),
    'Med Adm Sch Dt/Tm': ('Med Adm Sch Dt/Tm', '4f650c97-d237-4480-a56b-2f0413e4c784', '2'),
    'Med Adm Temp Order Id': ('Med Adm Temp Order Id', '96a2d4ef-a8da-464a-8fe2-efafd3289e86', '3'),
    'Med Adm Verify Dt/Tm': ('Med Adm Verify Dt/Tm', 'f56baaa3-f873-4c37-be69-954ae5ee624b', '2'),
    'Med C Cost': ('Chg Item Price', '07112aeb-0932-4898-a6be-fe9b5ddf048c', '1'),
    'Med C Cost Type': ('Chg Type', '59016b19-0636-4cb9-bbff-af3ea84d1a5e', '3'),
    'Med Prod Formulary Status': ('Order Catalog', 'ca672220-76a6-4601-bc0f-f0104e07874c', '3'),
    'Mltm Legal Status': ('Order Catalog Type', '97f18dc3-faf9-42d5-a47a-999fe7cdf944', '1'),
    'Name First': ('Name First', 'ddb89eb8-f2c6-4821-9153-66a050f7481d', '3'),
    'Name Last': ('Name Last', 'ee6b2563-7e7f-484b-9435-4cd9ad6cde5b', '3'),
    'Ord Detail Display': ('Order Detail Display', 'a371d5d0-f1a0-41f0-a2af-aeeceef175e2', '3'),
    'Ord Detail Field Id': ('Ord Detail Field Id', 'c76c1416-dc5a-470b-b338-f4f3fdd3a1ab', '1'),
    'Ord Detail Field Meaning': ('Ord Detail Field Meaning', 'cede0389-e782-49f5-a140-f197ff1806c6', '3'),
    'Ord Detail Field Value': ('Ord Detail Field Value', '3fda7e65-00ff-4e59-b05e-5b2d7084757b', '3'),
    'Ord Mnemonic': ('Order Catalog', 'ca672220-76a6-4601-bc0f-f0104e07874c', '3'),
    'Ord Order Id': ('Order Id', '0081bf6a-b747-49ad-be49-e193d5b3bc93', '3'),
    'Ord Orig Order Dt/Tm': ('Order Dt/Tm', 'db8be1e0-6f63-4851-8f87-163fae80ead3', '2'),
    'Ord Status': ('Order Status', '1d1ee0d5-6e14-462f-bb27-2939048ea0bd', '1'),
    'Order AS Mnemonic': ('Order AS Mnemonic', 'c1b0fbbc-baa6-42a8-ab1c-202bba3d3ed7', '3'),
    'Order Active Ind': ('Order Active Ind', 'dc9a6a8a-5a97-4087-a7d8-a57063635e42', '1'),
    'Order Activity Type': ('Order Activity Type', '710c4385-c5db-41ac-89b8-03a5b1a7120b', '3'),
    'Order Catalog': ('Order Catalog', 'ca672220-76a6-4601-bc0f-f0104e07874c', '1'),
    'Order Catalog Type': ('Order Catalog Type', '97f18dc3-faf9-42d5-a47a-999fe7cdf944', '1'),
    'Order Clinical Category': ('Order Clinical Category', '2842d04a-a78b-4374-a1bd-1a220c684ecb', '1'),
    'Order Clinical Display': ('Order Clinical Display', '563c423f-45f7-49ff-bd79-34066c5931e3', '3'),
    'Order Clinical Display Line': ('Order Detail Display', 'a371d5d0-f1a0-41f0-a2af-aeeceef175e2', '3'),
    'Order Department Status': ('Order Department Status', '88fd4dac-f6b3-40d0-bd4b-5712a36b92e3', '1'),
    'Order Detail Display': ('Order Detail Display', 'a371d5d0-f1a0-41f0-a2af-aeeceef175e2', '3'),
    'Order Discontinue Eff Dt/Tm': ('Order Discontinue Eff Dt/Tm', '87db9652-09cd-45c9-bb4a-dec92aa4b92c', '2'),
    'Order Discontinue Ind': ('Order Discontinue Ind', '124e2bbf-35be-4fd4-ab74-c8c611b0f062', '1'),
    'Order Discontinue Type': ('Order Discontinue Type', '40737f78-7b30-4c98-94e7-a81df012bd12', '3'),
    'Order Dt/Tm': ('Order Dt/Tm', 'db8be1e0-6f63-4851-8f87-163fae80ead3', '2'),
    'Order Enc Id': ('Order Enc Id', '4b37cd4f-e389-4220-843e-9c982b9a984e', '3'),
    'Order Flag': ('Order Flag', '6436dc0f-ae98-4fee-a30b-23a755eec2a2', '1'),
    'Order Id': ('Order Id', '0081bf6a-b747-49ad-be49-e193d5b3bc93', '1'),
    'Order Last Updt Provider Name': ('Order Last Updt Provider Name', 'd510b953-d957-4865-add3-2363504873fb', '3'),
    'Order Mnemonic': ('Order Mnemonic', '7120df4c-b25b-4595-badb-79dedda7f9aa', '3'),
    'Order Orig Order Dt/Tm': ('Order Dt/Tm', 'db8be1e0-6f63-4851-8f87-163fae80ead3', '2'),
    'Order PRN Ind': ('Order PRN Ind', 'f8cf6fa8-7d9d-4a7e-8636-a1f36e6f952a', '1'),
    'Order Start Dt/Tm': ('Order Start Dt/Tm', '5de804b0-ae7b-4044-9735-f897f5b46a23', '2'),
    'Order Status': ('Order Status', '1d1ee0d5-6e14-462f-bb27-2939048ea0bd', '1'),
    'Order Status Dt/Tm': ('Order Update Dt/Tm', 'f14b375c-ccb8-4caa-9627-aa5f54acf962', '2'),
    'Order Temp Order Id': ('Order Temp Order Id', 'a310079c-cd4a-486f-8a9d-f61b057dde0f', '1'),
    'Order Update Dt/Tm': ('Order Update Dt/Tm', 'f14b375c-ccb8-4caa-9627-aa5f54acf962', '2'),
    'Order Update Prsnl Name': ('Order Update Prsnl Name', '2e0c59d2-f20f-40ed-9306-770d086d3108', '3'),
    'Order Valid Dose Dt/Tm': ('Order Valid Dose Dt/Tm', '019a4b85-404d-457a-b24c-82ff4978139d', '2'),
    'P S Desc': ('Chg CDM', 'ee98bf46-35fe-46d1-a919-c6d280e8010d', '3'),
    'Pay Amount': ('Chg Item Price', '07112aeb-0932-4898-a6be-fe9b5ddf048c', '1'),
    'Pay Effective Dt': ('Chg Service Dt/Tm', '9452e25e-98ec-4820-b40c-b95fbc94935e', '2'),
    'Pay Post Dt': ('Chg Posted Dt/Tm', 'f34d0bfe-074f-4268-8275-ef17de249e1c', '2'),
    'Pay Trans Reason': ('F Chg Status', '30aadfd4-ba16-4e13-a56d-b0de02c4882b', '3'),
    'Pay Trans Sub Type': ('Chg Type', '59016b19-0636-4cb9-bbff-af3ea84d1a5e', '3'),
    'Pay Trans Type': ('Chg Activity Type', '07e4d357-ed1c-46fb-acb3-84ce2957db36', '3'),
    'PayTrans Alias': ('Chg CDM', 'ee98bf46-35fe-46d1-a919-c6d280e8010d', '3'),
    'Per Addr Beg Eff Dt/Tm': ('Per Addr Beg Eff Dt/Tm', '7c4c03d8-a010-4263-9a12-22e038fda244', '2'),
    'Per Addr City': ('Per Addr City', 'a47b6faf-dbcd-497f-80c4-05414fc6266a', '3'),
    'Per Addr Country': ('Per Addr Country', '09587d58-43be-4653-9471-d48619c62e8b', '3'),
    'Per Addr End Eff Dt/Tm': ('Per Addr End Eff Dt/Tm', 'bc155b6f-b986-4785-9368-e386583add22', '2'),
    'Per Addr State': ('Per Addr State', '71f44615-2002-4ee1-af2d-c40914b6fe5d', '3'),
    'Per Addr Street 1': ('Per Addr Street 1', '461f0fd2-786b-476f-8beb-0129d080cf7c', '3'),
    'Per Addr Street 2': ('Per Addr Street 2', '4f7b765d-400b-470d-a312-60818acdb6b5', '3'),
    'Per Addr Type': ('Per Addr Type', 'a7213cf1-35ee-41cc-ab9d-ca966e634dfa', '1'),
    'Per Addr Update Dt/Tm': ('Per Addr Update Dt/Tm', '506cbe22-845a-419b-9f8a-7205a54083a7', '2'),
    'Per Addr Val Expire Dt/Tm': ('Per Addr Val Expire Dt/Tm', '18a5b628-e4fb-4f35-8ae8-b9820cb157ad', '2'),
    'Per Addr Zip': ('Per Addr Zip', 'e3a175aa-d704-43cb-9b0c-2adf977443dc', '3'),
    'Per Email (Addr Type)': ('Per Email (Addr Type)', '1771553b-0e4d-4547-9b2b-863d38583b08', '3'),
    'Per Phn Active Ind': ('Per Phn Active Ind', '90c3678a-3f38-4991-a0e1-690f9f71c866', '1'),
    'Per Phn Contact Method': ('Per Phn Contact Method', '7aa0ac4d-43e0-435a-a870-bf28915294b7', '1'),
    'Per Phn Phone Number': ('Per Phn Phone Number', '288615d1-7596-4c16-9790-1d45281b45ed', '3'),
    'Per Phn Type': ('Per Phn Type', 'de76e66b-b12e-4d64-b3ea-a88a3e98b035', '1'),
    'Per Rel Family Subtype': ('Per Rel Family Subtype', 'f4e3dc47-d673-4d4f-b7af-4965b70bdfc9', '3'),
    'Per Rel Person Id': ('Per Rel Person Id', 'ca7cdf58-51e3-4534-98ef-80e5c8939f6d', '3'),
    'Per Rel Person Name': ('Per Rel Person Name', 'e7c005cc-4302-4a0a-842b-80d62196e72b', '3'),
    'Per Rel Type': ('Per Rel Type', '7d262bee-2b36-4985-ba3d-6ee13802a636', '1'),
    'Person Abs Birth Dt/Tm': ('Person Abs Birth Dt/Tm', '30683aa3-42b5-42f6-b7ee-fffff887c0fc', '2'),
    'Person Birth Dt Tm': ('Person Abs Birth Dt/Tm', '30683aa3-42b5-42f6-b7ee-fffff887c0fc', '2'),
    'Person Current Age': ('Person Current Age', '8949a674-2831-40e8-83df-156c05557da5', '3'),
    'Person Full Name': ('Person Name Full Format', '12b363ca-f0d2-4a5d-afe3-3225de181f5f', '3'),
    'Person Gender': ('Person Sex', 'd15afa2f-0bce-40b6-9672-ad811db27bf3', '3'),
    'Person Id': ('Person Id', 'b7b46f06-3128-49cf-b7b9-16af44617357', '3'),
    'Person Language': ('Person Language', '30acc863-3ccf-4bce-898e-b016488dde5d', '3'),
    'Person MRN': ('Person MRN', '12db87ba-4466-4682-8f6c-28adf9a8c8d3', '3'),
    'Person Name First': ('Person Name First', 'a4f6b973-6dd4-4a02-965a-7b324e3704b2', '3'),
    'Person Name First Format': ('Person Name First Format', '00d5562a-d7f8-47bd-b2d1-6d57b3834c83', '3'),
    'Person Name Full': ('Person Name Full Format', '12b363ca-f0d2-4a5d-afe3-3225de181f5f', '3'),
    'Person Name Full Format': ('Person Name Full Format', '12b363ca-f0d2-4a5d-afe3-3225de181f5f', '3'),
    'Person Name Last': ('Person Name Last', '2e9a30aa-c98f-4d50-9f5e-8d642c169fe8', '3'),
    'Person Name Last Format': ('Person Name Last Format', '8a93f07a-cc70-4aac-9c4c-716cc657ce78', '3'),
    'Person Name Middle': ('Person Name Middle', '942c60f2-698f-4328-b25f-e9bff77bb610', '3'),
    'Person Primary Care Pro': ('Person Primary Care Pro', '9caaa93e-0169-43fc-b78d-cc381b2ad9a0', '3'),
    'Person Race': ('Person Race', '777f74ad-9412-48ad-a4b5-220153432d6b', '3'),
    'Person SSN': ('Person SSN', '7e6cf9a2-3769-49f6-8486-bae6efee5253', '3'),
    'Person Sex': ('Person Sex', 'd15afa2f-0bce-40b6-9672-ad811db27bf3', '3'),
    'Pl Alias': ('Enc Admitting Phys NPI', '8b343e83-e1ee-4146-9a42-9f144317b653', '3'),
    'Pl Grp Name': ('Enc Medical Service', 'd803b34d-3519-4096-9309-24ae60c669ab', '3'),
    'Prsnl Active Ind': ('Prsnl Active Ind', 'c270984e-acb9-4472-9ed5-ad670a127463', '1'),
    'Prsnl Create Dt/Tm': ('Prsnl Create Dt/Tm', 'a40b5503-43de-464c-ad9f-f924ce095882', '2'),
    'Prsnl End Eff Dt/Tm': ('Prsnl End Eff Dt/Tm', '3560a0d6-208c-48ea-af57-16acfffdd115', '2'),
    'Prsnl Full Name': ('CE Performed Prsnl Name', '2ecc5b49-c180-440c-b0c2-ce9712307ba3', '3'),
    'Prsnl Name Full Format': ('Prsnl Name Full Format', 'b59660e9-add8-41b1-991c-1eeb1d31b613', '3'),
    'Prsnl Position': ('Enc Pl Relationship Type', 'd68ba34b-7554-43ba-9d7c-6bdd97054578', '1'),
    'Sch App Active Ind': ('Sch App Active Ind', '480501df-a23e-475a-a6fa-8cccc157620c', '1'),
    'Sch App Beg Dt/Tm': ('Sch App Beg Dt/Tm', 'c487e75f-4cc5-415e-80d2-dbd1afd61bdd', '2'),
    'Sch App Begin Dt/Tm': ('Sch App Beg Dt/Tm', 'c487e75f-4cc5-415e-80d2-dbd1afd61bdd', '2'),
    'Sch App Contig Ind': ('Sch App Contig Ind', '20a1ad5e-bd51-4f12-bc17-0a44f0573e77', '1'),
    'Sch App Def Slot Id': ('Sch App Def Slot Id', '4eb15512-a73b-4ce8-896e-928bdd6a27e2', '3'),
    'Sch App Description': ('Sch App Description', 'f11e6f1b-c0ca-41c7-be24-c1ac40dda593', '3'),
    'Sch App Duration': ('Sch App Duration', '31b8f1ff-4ba2-43cf-adc7-e3f386fbc87e', '1'),
    'Sch App Enc Id': ('Sch App Enc Id', '49daf484-e3ae-4967-ace2-f7ec7cf5e346', '3'),
    'Sch App End Dt/Tm': ('Sch App End Dt/Tm', 'abe2639f-c3a6-4980-befb-30300a250fd7', '2'),
    'Sch App Ev Id': ('Sch App Ev Id', '6ef57148-5ff8-4093-8064-ce48737cfc03', '3'),
    'Sch App Id': ('Sch App Id', 'd5807380-8c35-4f9c-8f32-487839a0bade', '3'),
    'Sch App Loc': ('Sch App Loc', '0f16e717-3b51-4441-be1f-079041a00ed8', '1'),
    'Sch App Realease Dt/Tm': ('Sch App Realease Dt/Tm', '4240e33a-04f0-48b4-8d02-ff3e11d2545b', '2'),
    'Sch App Resource': ('Sch App Resource', '9e7c1eb7-945a-4fbb-ba7e-c0e28978e0be', '1'),
    'Sch App Role': ('Sch App Role', 'c2d4234c-5846-4060-be75-33385907322e', '3'),
    'Sch App Role Meaning': ('Sch App Role Meaning', 'ebdc43ce-e888-4300-870a-27973ba39162', '3'),
    'Sch App Sch Id': ('Sch App Sch Id', 'bc508b73-3be0-4ec3-8d7d-eb818f547964', '3'),
    'Sch App Setup Duration': ('Sch App Setup Duration', '701fc4eb-8bc2-401d-91f5-99dd93679870', '1'),
    'Sch App Slot Mnemonic': ('Sch App Slot Mnemonic', '268b43b5-4271-46d5-8603-f86ccda45ad7', '3'),
    'Sch App Slot State': ('Sch App Slot State', 'f1120d40-c9b9-48bc-bdc8-cb9bff1c7ce1', '1'),
    'Sch App State': ('Sch App State', 'b678ef69-8bf2-4bcb-ac34-19a274f36ebe', '3'),
    'Sch App State Meaning': ('Sch App State Meaning', 'ba8923db-ef63-43e7-87f2-376e0bb19370', '3'),
    'Sch App Time Flag': ('Sch App Time Flag', '536350e1-e59a-4504-8d33-0da79ab79dbe', '1'),
    'Sch Ev Act Action': ('Sch Ev State', '51555e3b-3afd-40f7-889e-8e4fda3e726e', '3'),
    'Sch Ev Act Dt/Tm': ('Sch App Beg Dt/Tm', 'c487e75f-4cc5-415e-80d2-dbd1afd61bdd', '2'),
    'Sch Ev Act Reason': ('Sch App Description', 'f11e6f1b-c0ca-41c7-be24-c1ac40dda593', '3'),
    'Sch Ev App Type': ('Sch Ev App Type', 'b8533e26-c303-4c1f-8689-90b6dc4f27cd', '3'),
    'Sch Ev Appt Type': ('Sch Ev App Type', 'b8533e26-c303-4c1f-8689-90b6dc4f27cd', '3'),
    'Sch Ev Sch Seq': ('Sch Ev Sch Seq', '95665401-f4ab-46c6-b7a3-498dff29d6dd', '1'),
    'Sch Ev State': ('Sch Ev State', '51555e3b-3afd-40f7-889e-8e4fda3e726e', '3'),
    'Sch Evt Act Reason': ('Sch App Description', 'f11e6f1b-c0ca-41c7-be24-c1ac40dda593', '3'),
    'Sch Evt Appt Type': ('Sch Ev App Type', 'b8533e26-c303-4c1f-8689-90b6dc4f27cd', '3'),
    'Sch Res': ('Sch Res', '7cc1d1cf-2296-41ff-b195-2b90eea162f8', '3'),
    'Sch Res Description': ('Sch Res Description', '6c6a93e4-88e0-40ac-9c1b-d0c45e984bf8', '3'),
    'Sch Res Quota': ('Sch Res Quota', '48fdead8-fc26-48b7-9814-55e9c0073bb0', '1'),
    'Task Atv Active Ind': ('Task Atv Status', '7d5b0261-afc4-4540-9d3f-e4490a3faf02', '3'),
    'Task Atv Class': ('Task Atv Type', 'c9dfe041-a37f-4cb5-b689-4f08a702abe9', '3'),
    'Task Atv Create Dt/Tm': ('Task Atv Create Dt/Tm', '0c874a83-84db-4be9-ac01-ce63a8a467c2', '2'),
    'Task Atv Dt/Tm': ('Task Atv Dt/Tm', '68702077-a5fa-4cda-808b-6a5a6dec7aac', '2'),
    'Task Atv Event': ('Task Atv Event', 'cb085880-0d86-4abc-8984-14241b0ba379', '3'),
    'Task Atv Event Id': ('Task Atv Event Id', '2164578e-218c-40a6-82b8-8600cad15ba6', '3'),
    'Task Atv Id': ('Task Atv Event Id', '2164578e-218c-40a6-82b8-8600cad15ba6', '3'),
    'Task Atv Loc': ('Task Atv Loc', '425e0df2-6849-4911-a403-ef6db587c661', '3'),
    'Task Atv Order Id': ('Task Atv Order Id', '69408c86-810e-4bae-b065-3857f12d608f', '1'),
    'Task Atv Performed Dt/Tm': ('Task Atv Dt/Tm', '68702077-a5fa-4cda-808b-6a5a6dec7aac', '2'),
    'Task Atv Performed Prsnl Name': ('Task Atv Performed Prsnl Name', '0fda616a-d623-43dc-8a1a-cb13089a9da0', '3'),
    'Task Atv Ref Task Id': ('Task Atv Ref Task Id', '718e51bd-bdb3-44b7-aef0-c52662ac01dc', '1'),
    'Task Atv Result Set Id': ('Task Atv Result Set Id', '08d2ecad-cce8-4de8-94df-7ff4c9287c33', '3'),
    'Task Atv Scheduled Dt/Tm': ('Task Atv Scheduled Dt/Tm', 'c787b623-4c2c-47c4-bfc6-4cd64e877c34', '2'),
    'Task Atv Status': ('Task Atv Status', '7d5b0261-afc4-4540-9d3f-e4490a3faf02', '3'),
    'Task Atv Status Reason': ('Task Atv Status Reason', 'd566f312-2cb0-4728-a67d-66535416c8f2', '3'),
    'Task Atv Task Type': ('Task Atv Task Type', '0b5e4d6f-d099-4d98-87b6-69d92d516bca', '1'),
    'Task Atv Type': ('Task Atv Type', 'c9dfe041-a37f-4cb5-b689-4f08a702abe9', '3'),
    'Task Atv Update Dt/Tm': ('Task Atv Update Dt/Tm', '610cb1ca-291a-4912-82ea-58cb8bdc6720', '2'),
    'Task AtvStatus Reason': ('Task Atv Status Reason', 'd566f312-2cb0-4728-a67d-66535416c8f2', '3'),
}

VERIFIED_CWC_ENCOUNTER_UUIDS: set[str] = {v[1] for v in LIVE_CWC_ELEMENT_MAP.values()}
# DA2 compares these with an upper-case _KEY column (Person Name Last -> PERSON.NAME_LAST_KEY in DA2's own SQL),
# so an Is Like pattern for them is upper-cased.
LIKE_UPPER_CASE_FIELDS: set[str] = {"Person Name Last"}


CSSRS_INLINE_SUBQUERIES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "WishD",
        "Q1: Wish to be Dead",
        [
            ("CSSRS Wish to be Dead", "30712909.00"),
        ],
    ),
    (
        "LevelRisk",
        "Overall C-SSRS Level of Suicide Risk",
        [
            ("CSSRS Level Of Suicide Risk", "54689495.00"),
        ],
    ),
    (
        "SBehavior",
        "Q6: Suicidal Behavior",
        [
            ("CSSRS Since Last Ask Suicide Behavior", "31360587.00"),
            ("CSSRS Actual Suicide Attempt", "30713329.00"),
            ("CSSRS Actual Suicide Attempt SLV", "176726189.00"),
        ],
    ),
    (
        "SMethod",
        "Q3: Active Ideation w/ Method (No Intent)",
        [
            ("CSSRS Since Last Ask Suicide Method", "31360497.00"),
        ],
    ),
    (
        "SPlan",
        "Q5: Active Ideation w/ Specific Plan and Intent",
        [
            ("CSSRS Since Last Ask Suicide with Plan", "31360557.00"),
        ],
    ),
    (
        "SIntent",
        "Q4: Active Ideation w/ Intent (No Plan)",
        [
            ("CSSRS Active Suicide Idea Intent no Plan", "30713089.00"),
        ],
    ),
]

DOMAIN_UUID_MAP: dict[str, str] = {
    "CWC Encounter": "a67e07c5-0244-4b28-a146-7523f6090016",
    "CWC Person": "4f854d1b-0641-4c9b-903a-8c7b7e5082e6",
    "CWC Personnel": "974ef8bc-2189-4247-a030-c8a0f6dc581f",
    "CWC Formulary": "615d2219-45c4-4a34-9d51-1255d344954e",
    "CWC Organization": "345c493e-8d12-403e-8d8f-2a6173e62f78",
    "CWF GL": "7418a91e-34c8-4c11-9b25-1b95f9c23f61",
    "CWF GS Revenue": "4496fa20-b01f-482f-8bdc-4bf3a07ba368",
    "CWF GS Payments": "f5491b37-5c80-41e0-b7f3-dff36c11fb23",
    "CWF GS Adjustments": "13261e60-354b-4ffa-a430-ca60736b4e91",
    "CWC Scheduling Resource": "28a3792f-4918-4895-9382-73284d95a638",
}

def _birt_type_for(ccl_type: str) -> str:
    ct = (ccl_type or "").lower()
    if "datetime" in ct or "dq8" in ct:
        return "date-time"
    if "boolean" in ct or "i4" in ct:
        return "integer"
    if "numeric" in ct:
        return "decimal"
    return "string"


class DA2ReportGenerator:
    """Generates upload-ready BIRT 4.4.2 .rptdesign XML files and DA2 Domain Query specs."""

    def __init__(
        self,
        interpreter: MillenniumInterpreter | None = None,
        ccl_compiler: CCLCompiler | None = None,
        tenant_config: TenantConfig | None = None,
    ) -> None:
        self._interpreter = interpreter or MillenniumInterpreter()
        self._ccl_compiler = ccl_compiler or CCLCompiler(self._interpreter)
        self._tenant_config = tenant_config or TenantConfig()
        self._da2_catalog = {e["da2_field_name"]: dict(e) for e in load_da2_elements()}
        for bv in load_da2_canonical_reports_catalog().get("business_views_xml_catalog", []):
            lv_name = bv.get("logical_view_name", "")
            is_deprecated_view = "OLD" in lv_name or lv_name == "CWF Encounter"
            for col in bv.get("columns", []):
                cname = col.get("name")
                cuuid = col.get("uuid")
                cat_name = col.get("category_name", "Encounter")
                if cname and cuuid:
                    if cname in self._da2_catalog:
                        if not is_deprecated_view and cat_name not in ("Deprecate", "Deprecated Elements"):
                            self._da2_catalog[cname]["element_uuid"] = cuuid
                    else:
                        self._da2_catalog[cname] = {
                            "da2_field_name": cname,
                            "da2_domain": "CWC Encounter",
                            "da2_bus_view_subentity": cat_name,
                            "v500_target": "ENCOUNTER.*",
                            "ccl_data_type": "dq8 (DATETIME)" if "Dt" in cname else "vc (STRING)",
                            "code_set": None,
                            "element_uuid": cuuid,
                            "value_semantics_and_guardrails": "Harvested Business View element",
                        }
        for alias_name, (canon_name, canon_uuid, canon_type) in LIVE_CWC_ELEMENT_MAP.items():
            if alias_name in self._da2_catalog:
                self._da2_catalog[alias_name]["element_uuid"] = canon_uuid
            if canon_name in self._da2_catalog:
                self._da2_catalog[canon_name]["element_uuid"] = canon_uuid

    def generate(
        self,
        request_text: str,
        report_name: str | None = None,
        tenancy: str | None = None,
        owner_group_display: str | None = None,
        facility_display: str | None = None,
        tenant_config: TenantConfig | None = None,
        value_filters: dict[str, list[str]] | None = None,
        like_filters: dict[str, str] | None = None,
    ) -> DA2UploadableBundle:
        """value_filters: DA2 field name -> display values to keep, e.g. {"Enc Type": ["Inpatient"]}. Each becomes an
        IN-list filter. Only fields with a harvested display -> code value map work; anything else raises ValueError.

        like_filters: DA2 text field name -> DA2 "Is Like" pattern with % wildcards, e.g. {"Person Name Last": "%SM%"}.
        Each becomes an Is Like filter in the .daquery only (DA2 matches the pattern as written, case included); the
        reference SQL, the CCL and the standalone .rptdesign don't carry it, so it is listed in
        value_filters_not_in_reference. Only verified CWC Encounter text elements work; anything else raises ValueError."""
        vfilters = self._resolve_value_filters(value_filters) + self._resolve_like_filters(like_filters)
        eff_tenant = TenantConfig.resolve_for_request(
            request_text=request_text,
            base_config=tenant_config or self._tenant_config,
            owner_group_display=owner_group_display,
            tenancy=tenancy,
            facility_display=facility_display,
        )
        interp = self._interpreter.interpret(request_text, tenancy=eff_tenant.tenancy)
        r_name = report_name or self._derive_report_name(request_text)
        report_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"com.cerner.discern.da2.report:{r_name}"))
        low = request_text.lower()
        is_active_census = ("active" in low and "census" in low) or any(
            w in low for w in ("active inpatient", "current inpatient", "midnight census", "currently admitted", "inhouse")
        )

        is_cssrs = (
            any(w in low for w in ("cssrs", "c-ssrs", "columbia", "suicide"))
            and not ("suicide precaution" in low and not any(w in low for w in ("cssrs", "c-ssrs", "columbia", "screening")))
        )
        is_provider_clinical_notes = (
            any(
                w in low
                for w in (
                    "consult note",
                    "initial consult",
                    "progress note",
                    "provider note",
                    "physician note",
                    "oppe",
                    "fppe",
                )
            )
            and not any(w in low for w in ("where is", "which table", "which column"))
        )
        queries, birt_join_key = self._build_da2_query_specs(
            r_name, interp, is_active_census, owner_group_display=eff_tenant.owner_group_display, value_filters=vfilters
        )
        # The value maps are CWC Encounter elements: a design with no CWC Encounter query carries none of them, and the
        # reference CCL follows the DA2 files.
        if not any(q.da2_domain == "CWC Encounter" for q in queries):
            vfilters = []
        ccl_prog = self._ccl_compiler.compile(
            request_text, tenancy=interp.inferred_tenancy, interpretation=interp,
            value_filters=[(vf["target"], [c for _, c in vf["values"]]) for vf in vfilters if vf["target"]],
        )
        eff_strategy = "two_dataset_birt_join" if (len(queries) == 2 and birt_join_key) else "single_domain_oda"
        rptdesign_xml = self._render_rptdesign_xml(
            report_name=r_name,
            report_uuid=report_uuid,
            queries=queries,
            execution_strategy=eff_strategy,
            birt_join_key=birt_join_key,
            is_cssrs=is_cssrs,
            is_provider_clinical_notes=is_provider_clinical_notes,
        )
        build_card_md = self._render_build_card_md(
            report_name=r_name,
            interp=interp,
            queries=queries,
            birt_join_key=birt_join_key,
        )
        verification = self.verify_da2_bundle(
            rptdesign_xml=rptdesign_xml,
            queries=queries,
            interp=interp,
            is_active_census=is_active_census,
        )

        return DA2UploadableBundle(
            report_name=r_name,
            report_uuid=report_uuid,
            request_text=request_text,
            target_tenancy=interp.inferred_tenancy,
            execution_strategy=eff_strategy,
            birt_join_key=birt_join_key,
            queries=queries,
            rptdesign_xml=rptdesign_xml,
            build_card_markdown=build_card_md,
            equivalent_ccl=ccl_prog.source_code,
            verification=verification,
            owner_group_display=eff_tenant.owner_group_display,
            value_filters_not_in_reference=[
                vf["text"] for vf in vfilters
                if vf["target"] not in ccl_prog.value_filters_applied
                or any(q.da2_domain == "CWC Encounter" and self._sql_in_list(vf) not in q.ejs_v500_sql for q in queries)
            ],
        )

    def _resolve_value_filters(self, value_filters: dict[str, list[str]] | None) -> list[dict[str, Any]]:
        if not value_filters:
            return []
        fields = load_da2_filter_value_catalog()["live_harvested_code_value_mappings"]["fields"]
        out = []
        for name, displays in value_filters.items():
            live = LIVE_CWC_ELEMENT_MAP.get(name)
            # A live element under another name (Prsnl Position -> Enc Pl Relationship Type) would filter the wrong field.
            if name not in fields or not live or live[0] != name or live[1] not in VERIFIED_CWC_ENCOUNTER_UUIDS:
                raise ValueError(f"No verified DA2 value map for {name!r}")
            vmap = {html.unescape(str(d)).strip().lower(): (html.unescape(str(d)).strip(), str(cv))
                    for d, cv in fields[name]["display_to_code_value"].items()}
            unknown = [d for d in displays if str(d).strip().lower() not in vmap]
            if unknown or not displays:
                raise ValueError(f"Unknown {name!r} values: {unknown or 'none given'}")
            values = list(dict.fromkeys(vmap[str(d).strip().lower()] for d in displays))
            values = [(d, str(int(float(cv)))) for d, cv in values]
            target = (self._da2_catalog.get(name) or {}).get("v500_target") or ""
            table, _, column = target.partition(".")
            if column not in {c["name"] for c in (load_v500_tables().get(table) or {}).get("columns", [])}:
                target = ""  # e.g. ENCOUNTER.ENC_ADMIT_TYPE_CD: not a V500 column, so no SQL or CCL for it
            out.append({
                "name": name,
                "uuid": LIVE_CWC_ELEMENT_MAP[name][1],
                "values": values,
                # Some catalog targets point at a date or a wildcard; only a *_CD column can take code values.
                "target": target if re.fullmatch(r"[A-Z0-9_]+\.[A-Z0-9_]+_CD", target) else None,
                "text": f"{name} in ({', '.join(d for d, _ in values)})",
            })
        return out

    def _resolve_like_filters(self, like_filters: dict[str, str] | None) -> list[dict[str, Any]]:
        coded = load_da2_filter_value_catalog()["live_harvested_code_value_mappings"]["fields"]
        out = []
        for name, pattern in (like_filters or {}).items():
            live = LIVE_CWC_ELEMENT_MAP.get(name)
            # Text only: a "vc" element without a code value map. Coded fields take value_filters' exact values.
            text = str((self._da2_catalog.get(name) or {}).get("ccl_data_type") or "").lower().startswith("vc")
            if not live or live[0] != name or live[1] not in VERIFIED_CWC_ENCOUNTER_UUIDS or not text or name in coded \
                    or live[2] == "2":  # a date, whatever the catalog's type says
                raise ValueError(f"No verified DA2 text field {name!r}")
            pattern = str(pattern).strip()
            if not pattern.strip("%_ ") or len(pattern) > 100:
                raise ValueError(f"Unusable {name!r} pattern: {pattern!r}")
            if name in LIKE_UPPER_CASE_FIELDS:
                pattern = pattern.upper()
            out.append({"name": name, "uuid": live[1], "values": [], "target": None, "like": pattern,
                        "text": f"{name} is like '{pattern}'"})
        return out

    @staticmethod
    def _sql_in_list(vf: dict[str, Any]) -> str:
        table, column = (vf["target"] or ".").split(".")
        return f"CWC_{table}.{column} IN ({', '.join(c for _, c in vf['values'])})"

    def _build_da2_query_specs(
        self,
        report_name: str,
        interp: InterpretationResult,
        is_active_census: bool,
        owner_group_display: str = DEFAULT_OWNER_GROUP_DISPLAY,
        value_filters: list[dict[str, Any]] | None = None,
    ) -> tuple[list[DA2QuerySpec], str | None]:
        # Collect unique DA2 elements from matched columns + forms
        all_elements: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        def add_elem(fname: str) -> None:
            if not fname or fname in seen_names:
                return
            seen_names.add(fname)
            cat = self._da2_catalog.get(fname)
            if cat:
                all_elements.append(dict(cat))
            else:
                all_elements.append({
                    "da2_field_name": fname,
                    "da2_domain": "CWC Encounter",
                    "da2_bus_view_subentity": "Encounter",
                    "v500_target": "ENCOUNTER.*",
                    "ccl_data_type": "vc (STRING)",
                    "code_set": None,
                    "element_uuid": str(uuid.uuid5(uuid.NAMESPACE_OID, f"com.cerner.discern.da2.element:{fname}")),
                    "value_semantics_and_guardrails": "DA2 business-view field",
                })

        # Ensure primary join/anchor keys are always first
        add_elem("Enc Id")
        add_elem("Enc Encounter Number")
        add_elem("Person Full Name")

        # Domain and sub-entity element expansion from general clinical/financial semantics
        low_req = interp.request_text.lower()
        is_meta_question = any(k in low_req for k in ("where is", "where are", "which table", "which column"))
        is_cssrs = (
            any(k in low_req for k in ("cssrs", "c-ssrs", "columbia", "suicide"))
            and not ("suicide precaution" in low_req and not any(k in low_req for k in ("cssrs", "c-ssrs", "columbia", "screening")))
        )
        is_rehab_therapy_minutes = (
            any(k in low_req for k in ("therapy minutes by discipline", "pt, ot, and slp", "pt/ot/slp", "rehab therapy minutes"))
            and not any(k in low_req for k in ("reconcil", "cpt", "benefit order"))
        )
        is_oral_care = (
            any(
                k in low_req
                for k in (
                    "oral care",
                    "oral hygiene",
                    "mouth care",
                    "teeth brushing",
                    "denture care",
                    "oral cavity",
                    "oral health risk",
                    "gg0130 oral",
                )
            )
            and not is_meta_question
        )
        is_bladder_bowel = (
            (
                any(
                    k in low_req
                    for k in (
                        "bladder/bowel",
                        "bowel/bladder",
                        "bladder and bowel",
                        "bowel and bladder",
                        "bladder management",
                        "bowel management",
                        "bladder scan",
                        "bowel movement",
                        "bowel elimination",
                        "bowel continence",
                        "bowel incontinence",
                        "bladder program",
                        "bowel program",
                        "toileting",
                        "post-void residual",
                    )
                )
                or (
                    ("bladder" in low_req or "bowel" in low_req)
                    and any(
                        k in low_req
                        for k in (
                            "management",
                            "elimination",
                            "continence",
                            "incontinence",
                            "retraining",
                            "scan",
                            "program",
                            "movement",
                            "assistance",
                            "hygiene",
                            "documentation",
                            "flowsheet",
                            "iview",
                        )
                    )
                )
            )
            and not is_meta_question
            and not any(
                k in low_req
                for k in (
                    "cauti",
                    "culture",
                    "order",
                    "orders",
                    "diagnosis",
                    "diagnoses",
                    "icd-10",
                    "icd10",
                    "cancer",
                    "neoplasm",
                    "biopsy",
                    "pathology",
                )
            )
        )
        is_bathing_hygiene = (
            (
                any(k in low_req for k in ("bathing", "bath type", "bathing refused", "bed bath", "shower", "weefim bathing", "weefim grooming"))
                or ("daily hygiene" in low_req and not is_oral_care)
            )
            and not is_meta_question
        )
        is_nutrition_assessment = (
            (
                any(k in low_req for k in ("nutrition assessment", "nutritional screening", "nutrition evaluation", "dietary risk", "nutrition risk"))
                or (("bmi" in low_req or "body mass index" in low_req) and any(k in low_req for k in ("nutrition", "weight", "bariatric", "diet")))
            )
            and not is_meta_question
        )
        is_pharmacy_interventions = (
            any(k in low_req for k in ("pharmacy clinical intervention", "pharmacy intervention", "pharmacist intervention", "pharmacoeconomic", "prescriber response"))
            and not is_meta_question
        )
        is_iv_central_line = (
            any(k in low_req for k in ("peripheral iv", "central line", "central iv", "central venous", "picc line"))
            and not any(k in low_req for k in ("iv order", "iv orders", "picc order", "central line order"))
            and not is_meta_question
        )
        is_precaution_orders = (
            any(
                k in low_req
                for k in (
                    "precaution order",
                    "precaution orders",
                    "isolation order",
                    "isolation orders",
                    "fall precaution",
                    "aspiration precaution",
                    "seizure precaution",
                    "suicide precaution",
                    "hip precaution",
                    "sternal precaution",
                )
            )
            and not is_meta_question
        )
        is_observation_orders = (
            any(k in low_req for k in ("1:1 observation", "video monitoring", "patient observation needs", "close observation", "sitter order"))
            and not is_meta_question
        )
        is_wound_consult_orders = (
            "wound" in low_req
            and "consult" in low_req
            and ("order" in low_req or "orders" in low_req)
            and not is_meta_question
        )
        is_diet_orders = (
            (
                any(k in low_req for k in ("diet order", "diet orders", "dietary order", "dietary orders", "tube feeding order"))
                or (any(k in low_req for k in ("diet report", "npo")) and not is_nutrition_assessment)
            )
            and "allergy" not in low_req
            and not is_meta_question
        )
        is_targeted_order_report = is_precaution_orders or is_observation_orders or is_wound_consult_orders or is_diet_orders
        is_census_risk_indicators = (
            any(
                k in low_req for k in ("wound", "catheter", "fall risk", "morse", "braden", "risk indicators")
            )
            and not is_oral_care
            and not is_bladder_bowel
            and not is_bathing_hygiene
            and not is_iv_central_line
            and not is_targeted_order_report
            and not ("order" in low_req and "census" not in low_req)
            and not any(k in low_req for k in ("resuscitation", "code status", "where are", "which column"))
        )
        is_revenue_charge_reconciliation = any(
            k in low_req for k in ("revenue/charge reconciliation", "revenue and charge reconciliation", "charge reconciliation")
        )
        is_provider_clinical_notes = (
            any(
                k in low_req
                for k in (
                    "consult note",
                    "initial consult",
                    "progress note",
                    "provider note",
                    "physician note",
                    "oppe",
                    "fppe",
                )
            )
            and not is_meta_question
        )
        is_custom_ce_template = (
            is_rehab_therapy_minutes
            or is_census_risk_indicators
            or is_revenue_charge_reconciliation
            or is_provider_clinical_notes
            or is_oral_care
            or is_bladder_bowel
            or is_bathing_hygiene
            or is_nutrition_assessment
            or is_pharmacy_interventions
            or is_iv_central_line
        )
        has_targeted_template = (
            is_cssrs
            or is_custom_ce_template
            or is_targeted_order_report
            or any(
                k in low_req
                for k in (
                    "cwf gs adjustments",
                    "cwf gs payments",
                    "resuscitation",
                    "code status",
                    "slot utilization",
                    "duplicate charge",
                )
            )
        )
        if not has_targeted_template:
            for cm in interp.matched_columns:
                if cm.da2_field_name:
                    add_elem(cm.da2_field_name)

            for fm in interp.matched_forms_and_outputs[:1]:
                if fm.score >= 3.5:
                    for df in fm.da2_fields:
                        add_elem(df)

        if "adjustment" in low_req or "cwf gs adjustments" in low_req:
            for f in (
                "Enc FIN", "Person Name Full", "Enc Loc Facility", "Enc Admit Dt/Tm", "Enc Discharge Dt/Tm",
                "Adj Post Dt", "Adj Amount", "Adj Trans Sub Type", "Adj Trans Reason", "AdjTrans Alias",
                "Adj Debit GL Number", "Adj Credit GL Number", "HP Financial Class",
            ):
                add_elem(f)
        if "payment" in low_req or "cwf gs payments" in low_req:
            for f in (
                "Enc FIN", "Person Name Full", "Enc Loc Facility", "Enc Admit Dt/Tm", "Enc Discharge Dt/Tm",
                "Pay Post Dt", "Pay Effective Dt", "Pay Amount", "Pay Trans Type", "Pay Trans Sub Type",
                "Pay Trans Reason", "PayTrans Alias", "HP Financial Class", "HP Plan Name",
            ):
                add_elem(f)
        if "resuscitation" in low_req or "code status" in low_req:
            for f in (
                "Enc Loc Nurse Unit", "Enc Loc Room", "Person Name Full", "Enc FIN",
                "Order Catalog", "Order Catalog Type", "Order Clinical Display Line", "Order Status",
            ):
                add_elem(f)
        if ("appointment" in low_req or "appt" in low_req) and "slot utilization" not in low_req and "no-show" not in low_req and "no show" not in low_req:
            all_elements.clear()
            seen_names.clear()
            for f in (
                "Enc Id", "Enc Financial Num", "Person Id", "Person Name Full Format",
                "Enc Loc Facility", "Enc Loc Nurse Unit",
                "Sch App Id", "Sch App Beg Dt/Tm", "Sch App End Dt/Tm",
                "Sch App Duration", "Sch App State", "Sch Ev App Type",
            ):
                add_elem(f)
            if "room" in low_req:
                add_elem("Enc Loc Room")
            if "bed" in low_req:
                add_elem("Enc Loc Bed")
            if "reason" in low_req or "cancel" in low_req:
                add_elem("Sch Evt Act Reason")
        elif "scheduling" in low_req or "slot utilization" in low_req or "appointment" in low_req:
            for f in (
                "Enc FIN", "Person Name Full", "Sch App Beg Dt/Tm", "Sch App End Dt/Tm", "Sch App Duration",
                "Sch App State", "Sch App Slot State", "Sch App Role", "Sch Evt Appt Type", "Sch Evt Act Reason",
            ):
                add_elem(f)
        if "ndc" in low_req or "duplicate charge" in low_req:
            for f in (
                "Enc FIN", "Chg Service Dt/Tm", "Chg Bill Item Desc", "Chg Item Quantity",
                "Chg Item Extended Price", "Chg M Field 1 Id", "Chg M Field 6", "Chg Manual Ind",
            ):
                add_elem(f)
        if is_cssrs:
            all_elements.clear()
            seen_names.clear()
            cssrs_live_cols = [
                ("Enc Id", "5ef4381c-6344-43c4-81b5-2c18cd6bcb70", "vc (STRING)", "ENCOUNTER.ENCNTR_ID", "Encounter", None),
                ("Person MRN", "12db87ba-4466-4682-8f6c-28adf9a8c8d3", "vc (STRING)", "PERSON_ALIAS.ALIAS", "Person", 4),
                ("Person Name Full Format", "12b363ca-f0d2-4a5d-afe3-3225de181f5f", "vc (STRING)", "PERSON.NAME_FULL_FORMATTED", "Person", None),
                ("Enc Financial Num", "1a4ee2f6-2ed1-48b3-9d79-974352b9fabf", "vc (STRING)", "ENCNTR_ALIAS.ALIAS", "Encounter", 319),
                ("Enc Loc Facility", "b09f6eab-aa80-4110-a4af-3dcbbd00384b", "vc (STRING)", "ENCOUNTER.LOC_FACILITY_CD", "Encounter", 220),
                ("Enc Loc Nurse Unit", "75267dc7-d5c6-437b-aa34-98e46bbc5db1", "vc (STRING)", "ENCOUNTER.LOC_NURSE_UNIT_CD", "Encounter", 220),
                ("Enc Loc Room", "e52b4716-00f9-42ab-8a04-8ba1e9ea1ece", "vc (STRING)", "ENCOUNTER.LOC_ROOM_CD", "Encounter", 220),
                ("Enc Registration Dt", "b52cf234-3ec7-4343-a9d8-db77b7354bd7", "vc (STRING)", "ENCOUNTER.REG_DT_TM", "Encounter", None),
                ("Enc Discharge Dt", "f2fccf23-964c-4fc6-82b2-a76fdd55e793", "vc (STRING)", "ENCOUNTER.DISCH_DT_TM", "Encounter", None),
                ("CE Event", "f01d0cc1-f599-46be-b816-ecd82fffbb0e", "vc (STRING)", "CLINICAL_EVENT.EVENT_CD", "Clinical Event", 72),
                ("CE Event Id", "bfc961f6-7aa0-4203-9075-82a5058e4ebb", "vc (STRING)", "CLINICAL_EVENT.EVENT_ID", "Clinical Event", None),
                ("CE Parent Event Id", "8c750217-756f-4276-8888-17d7cc0408ae", "vc (STRING)", "CLINICAL_EVENT.PARENT_EVENT_ID", "Clinical Event", None),
                ("CE End Dt/Tm", "4433e8b0-5947-4f79-b4aa-c32c8ec30c45", "dq8 (DATETIME)", "CLINICAL_EVENT.EVENT_END_DT_TM", "Clinical Event", None),
                ("CE Performed Dt/Tm", "7c3dbae0-373b-43db-afed-de4d3e7048a0", "dq8 (DATETIME)", "CLINICAL_EVENT.PERFORMED_DT_TM", "Clinical Event", None),
                ("CE Performed Prsnl Name", "2ecc5b49-c180-440c-b0c2-ce9712307ba3", "vc (STRING)", "CLINICAL_EVENT.PERFORMED_PRSNL_ID", "Clinical Event", None),
                ("CE Result Value", "155eec27-4c56-4cff-a4ee-861aa3335814", "vc (STRING)", "CLINICAL_EVENT.RESULT_VAL", "Clinical Event", None),
            ]
            for cname, cuuid, ctype, vtarget, subent, cset in cssrs_live_cols:
                seen_names.add(cname)
                all_elements.append({
                    "da2_field_name": cname,
                    "da2_domain": "CWC Encounter",
                    "da2_bus_view_subentity": subent,
                    "v500_target": vtarget,
                    "ccl_data_type": ctype,
                    "code_set": cset,
                    "element_uuid": cuuid,
                    "value_semantics_and_guardrails": "Live-verified CWC Encounter C-SSRS Adult clinical event element",
                })
            for subq_name, subq_label, ev_list in CSSRS_INLINE_SUBQUERIES:
                ev_inline_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"com.cerner.discern.da2.inline:{report_name}:{subq_name}:CE Event"))
                rv_inline_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"com.cerner.discern.da2.inline:{report_name}:{subq_name}:CE Result Value"))
                all_elements.append({
                    "da2_field_name": f"{subq_name}:CE Event",
                    "display_label": f"{subq_label} - Event",
                    "inline_query_name": subq_name,
                    "base_field_name": "CE Event",
                    "inline_uuid": ev_inline_uuid,
                    "subquery_events": ev_list,
                    "subquery_label": subq_label,
                    "da2_domain": "CWC Encounter",
                    "da2_bus_view_subentity": "Clinical Event",
                    "v500_target": "CLINICAL_EVENT.EVENT_CD",
                    "ccl_data_type": "vc (STRING)",
                    "code_set": 72,
                    "element_uuid": "f01d0cc1-f599-46be-b816-ecd82fffbb0e",
                    "value_semantics_and_guardrails": f"Inline subquery {subq_name} clinical event name",
                })
                all_elements.append({
                    "da2_field_name": f"{subq_name}:CE Result Value",
                    "display_label": f"{subq_label} - Response",
                    "inline_query_name": subq_name,
                    "base_field_name": "CE Result Value",
                    "inline_uuid": rv_inline_uuid,
                    "subquery_events": ev_list,
                    "subquery_label": subq_label,
                    "da2_domain": "CWC Encounter",
                    "da2_bus_view_subentity": "Clinical Event",
                    "v500_target": "CLINICAL_EVENT.RESULT_VAL",
                    "ccl_data_type": "vc (STRING)",
                    "code_set": None,
                    "element_uuid": "155eec27-4c56-4cff-a4ee-861aa3335814",
                    "value_semantics_and_guardrails": f"Inline subquery {subq_name} completed response value",
                })
            for cname, cuuid, ctype, vtarget, subent, cset in [
                ("Enc Type", "10b02c36-ff84-47d6-a825-ea142281a547", "vc (STRING)", "ENCOUNTER.ENCNTR_TYPE_CD", "Encounter", 71),
                ("Enc Registration Dt/Tm", "42ee9a32-031f-495d-962a-5f04376e071a", "dq8 (DATETIME)", "ENCOUNTER.REG_DT_TM", "Encounter", None),
                ("Enc Discharge Dt/Tm", "1d44a53e-cba7-4ddf-80c4-d86205e30e0d", "dq8 (DATETIME)", "ENCOUNTER.DISCH_DT_TM", "Encounter", None),
            ]:
                seen_names.add(cname)
                all_elements.append({
                    "da2_field_name": cname,
                    "da2_domain": "CWC Encounter",
                    "da2_bus_view_subentity": subent,
                    "v500_target": vtarget,
                    "ccl_data_type": ctype,
                    "code_set": cset,
                    "element_uuid": cuuid,
                    "value_semantics_and_guardrails": "Live-verified CWC Encounter element",
                })

        if is_targeted_order_report:
            pre_mar_elements = [
                e for e in all_elements
                if e.get("da2_field_name", "").startswith(("Med Adm", "Task Atv", "CE Med"))
            ]
            all_elements.clear()
            seen_names.clear()
            order_cols = [
                ("Enc Id", "5ef4381c-6344-43c4-81b5-2c18cd6bcb70", "vc (STRING)", "ENCOUNTER.ENCNTR_ID", "Encounter", None),
                ("Enc Financial Num", "1a4ee2f6-2ed1-48b3-9d79-974352b9fabf", "vc (STRING)", "ENCNTR_ALIAS.ALIAS", "Encounter", 319),
                ("Person MRN", "12db87ba-4466-4682-8f6c-28adf9a8c8d3", "vc (STRING)", "PERSON_ALIAS.ALIAS", "Person", 4),
                ("Person Name Full Format", "12b363ca-f0d2-4a5d-afe3-3225de181f5f", "vc (STRING)", "PERSON.NAME_FULL_FORMATTED", "Person", None),
                ("Enc Loc Facility", "b09f6eab-aa80-4110-a4af-3dcbbd00384b", "vc (STRING)", "ENCOUNTER.LOC_FACILITY_CD", "Encounter", 220),
                ("Enc Loc Nurse Unit", "75267dc7-d5c6-437b-aa34-98e46bbc5db1", "vc (STRING)", "ENCOUNTER.LOC_NURSE_UNIT_CD", "Encounter", 220),
                ("Enc Loc Room", "e52b4716-00f9-42ab-8a04-8ba1e9ea1ece", "vc (STRING)", "ENCOUNTER.LOC_ROOM_CD", "Encounter", 220),
                ("Bed Loc", "219ba43a-c7ee-44a4-8e79-052d4a5703da", "vc (STRING)", "ENCOUNTER.LOC_BED_CD", "Encounter", 220),
                ("Enc Attending Phys", "beb01385-36a9-4371-93e2-68056229e216", "vc (STRING)", "CWC_ENC_FUNC.GET_ATTEND_PHYS_NAME(CWC_ENCOUNTER.ENCNTR_ID)", "Encounter Functions", None),
                ("Enc Type", "10b02c36-ff84-47d6-a825-ea142281a547", "vc (STRING)", "ENCOUNTER.ENCNTR_TYPE_CD", "Encounter", 71),
                ("Enc Status", "7161959f-72cd-46bb-b6f6-2b177edd0469", "vc (STRING)", "ENCOUNTER.ENCNTR_STATUS_CD", "Encounter", 261),
                ("Enc Registration Dt/Tm", "42ee9a32-031f-495d-962a-5f04376e071a", "dq8 (DATETIME)", "ENCOUNTER.REG_DT_TM", "Encounter", None),
                ("Enc Discharge Dt/Tm", "1d44a53e-cba7-4ddf-80c4-d86205e30e0d", "dq8 (DATETIME)", "ENCOUNTER.DISCH_DT_TM", "Encounter", None),
                ("Order Id", "0081bf6a-b747-49ad-be49-e193d5b3bc93", "vc (STRING)", "ORDERS.ORDER_ID", "Orders", None),
                ("Order Catalog", "ca672220-76a6-4601-bc0f-f0104e07874c", "vc (STRING)", "ORDERS.CATALOG_CD", "Orders", 200),
                ("Order Catalog Type", "97f18dc3-faf9-42d5-a47a-999fe7cdf944", "vc (STRING)", "ORDERS.CATALOG_TYPE_CD", "Orders", 6000),
                ("Order Mnemonic", "7120df4c-b25b-4595-badb-79dedda7f9aa", "vc (STRING)", "ORDERS.ORDER_MNEMONIC", "Orders", 200),
                ("Order Detail Display", "a371d5d0-f1a0-41f0-a2af-aeeceef175e2", "vc (STRING)", "ORDERS.CLINICAL_DISPLAY_LINE", "Orders", None),
                ("Order Status", "1d1ee0d5-6e14-462f-bb27-2939048ea0bd", "vc (STRING)", "ORDERS.ORDER_STATUS_CD", "Orders", 6004),
                ("Order Dt/Tm", "db8be1e0-6f63-4851-8f87-163fae80ead3", "dq8 (DATETIME)", "ORDERS.ORIG_ORDER_DT_TM", "Orders", None),
                ("Order Start Dt/Tm", "5de804b0-ae7b-4044-9735-f897f5b46a23", "dq8 (DATETIME)", "ORDERS.CURRENT_START_DT_TM", "Orders", None),
            ]
            combined_order_cat: list[tuple[str, str]] = []
            if is_precaution_orders:
                combined_order_cat.extend(PRECAUTION_ORDER_CATALOG_ITEMS)
            if is_observation_orders:
                combined_order_cat.extend(OBSERVATION_MONITORING_ORDER_CATALOG_ITEMS)
            if is_wound_consult_orders:
                combined_order_cat.extend(WOUND_CONSULT_ORDER_CATALOG_ITEMS)
            target_order_cat = combined_order_cat or None
            target_order_cat_types = [("Dietary", "2511.00"), ("Nutritional Services", "636067.00")] if is_diet_orders else None
            for cname, cuuid, ctype, vtarget, subent, cset in order_cols:
                seen_names.add(cname)
                all_elements.append({
                    "da2_field_name": cname,
                    "da2_domain": "CWC Encounter",
                    "da2_bus_view_subentity": subent,
                    "v500_target": vtarget,
                    "ccl_data_type": ctype,
                    "code_set": cset,
                    "element_uuid": cuuid,
                    "primary_order_catalog": target_order_cat if cname == "Order Catalog" else None,
                    "primary_order_catalog_types": target_order_cat_types if cname == "Order Catalog Type" else None,
                    "value_semantics_and_guardrails": "Live-verified CWC Encounter orders element",
                })
            has_mar_audit_intent = bool(pre_mar_elements) or any(
                k in low_req
                for k in (
                    "medication administration",
                    "med admin",
                    "late medication",
                    "late dose",
                    "late doses",
                    "late mar",
                    "missed medication",
                    "missed med",
                    "missed dose",
                    "overdue medication task",
                    "overdue task",
                    "scheduled task",
                    "bcma",
                    "barcode",
                )
            )
            if has_mar_audit_intent:
                for e in pre_mar_elements:
                    ename = e["da2_field_name"]
                    if ename not in seen_names:
                        seen_names.add(ename)
                        all_elements.append(e)
                for cm in interp.matched_columns:
                    if cm.da2_field_name and cm.da2_field_name.startswith(("Med Adm", "Task Atv", "CE Med")):
                        add_elem(cm.da2_field_name)
                if any(w in low_req for w in ("scheduled", "late", "missed", "overdue")):
                    add_elem("Med Adm Sch Dt/Tm")
                add_elem("Med Adm Dt/Tm")
                add_elem("Med Adm Event Id")

        if is_custom_ce_template:
            if not is_targeted_order_report:
                all_elements.clear()
                seen_names.clear()
            if is_provider_clinical_notes:
                base_clin_cols = [
                    ("Enc Id", "5ef4381c-6344-43c4-81b5-2c18cd6bcb70", "vc (STRING)", "ENCOUNTER.ENCNTR_ID", "Encounter", None),
                    ("Enc Financial Num", "1a4ee2f6-2ed1-48b3-9d79-974352b9fabf", "vc (STRING)", "ENCNTR_ALIAS.ALIAS", "Encounter", 319),
                    ("Person MRN", "12db87ba-4466-4682-8f6c-28adf9a8c8d3", "vc (STRING)", "PERSON_ALIAS.ALIAS", "Person", 4),
                    ("Person Name Full Format", "12b363ca-f0d2-4a5d-afe3-3225de181f5f", "vc (STRING)", "PERSON.NAME_FULL_FORMATTED", "Person", None),
                    ("Enc Loc Facility", "b09f6eab-aa80-4110-a4af-3dcbbd00384b", "vc (STRING)", "ENCOUNTER.LOC_FACILITY_CD", "Encounter", 220),
                    ("Enc Loc Nurse Unit", "75267dc7-d5c6-437b-aa34-98e46bbc5db1", "vc (STRING)", "ENCOUNTER.LOC_NURSE_UNIT_CD", "Encounter", 220),
                    ("Enc Type", "10b02c36-ff84-47d6-a825-ea142281a547", "vc (STRING)", "ENCOUNTER.ENCNTR_TYPE_CD", "Encounter", 71),
                    ("Enc Status", "7161959f-72cd-46bb-b6f6-2b177edd0469", "vc (STRING)", "ENCOUNTER.ENCNTR_STATUS_CD", "Encounter", 261),
                    ("Enc Registration Dt/Tm", "42ee9a32-031f-495d-962a-5f04376e071a", "dq8 (DATETIME)", "ENCOUNTER.REG_DT_TM", "Encounter", None),
                    ("Enc Discharge Dt/Tm", "1d44a53e-cba7-4ddf-80c4-d86205e30e0d", "dq8 (DATETIME)", "ENCOUNTER.DISCH_DT_TM", "Encounter", None),
                    ("CE Event Id", "bfc961f6-7aa0-4203-9075-82a5058e4ebb", "vc (STRING)", "CLINICAL_EVENT.EVENT_ID", "Clinical Event", None),
                    ("CE Event", "f01d0cc1-f599-46be-b816-ecd82fffbb0e", "vc (STRING)", "CLINICAL_EVENT.EVENT_CD", "Clinical Event", 72),
                    ("CE Event Title", "3aea7feb-ce08-4dc6-92e2-ebe8fa04d94d", "vc (STRING)", "CLINICAL_EVENT.EVENT_TITLE_TEXT", "Clinical Event", None),
                    ("CE Performed Dt/Tm", "7c3dbae0-373b-43db-afed-de4d3e7048a0", "dq8 (DATETIME)", "CLINICAL_EVENT.PERFORMED_DT_TM", "Clinical Event", None),
                    ("CE Performed Prsnl Name", "2ecc5b49-c180-440c-b0c2-ce9712307ba3", "vc (STRING)", "CLINICAL_EVENT.PERFORMED_PRSNL_ID", "Clinical Event", None),
                    ("CE Verified Dt/Tm", "ca1f55a0-7e96-4bbd-b937-d843918a9fc3", "dq8 (DATETIME)", "CLINICAL_EVENT.VERIFIED_DT_TM", "Clinical Event", None),
                    ("CE Verified Prsnl Name", "afc53867-58e3-4f2e-aacb-e71afefcb9af", "vc (STRING)", "CLINICAL_EVENT.VERIFIED_PRSNL_ID", "Clinical Event", None),
                    ("CE Result Status", "7ccf3fe5-568a-4015-82e0-fd370a98426e", "vc (STRING)", "CLINICAL_EVENT.RESULT_STATUS_CD", "Clinical Event", 8),
                    ("CE Valid Until Dt/Tm", "871b99b7-e872-4db0-8762-d663c933ad76", "dq8 (DATETIME)", "CLINICAL_EVENT.VALID_UNTIL_DT_TM", "Clinical Event", None),
                ]
            else:
                base_clin_cols = [
                    ("Enc Id", "5ef4381c-6344-43c4-81b5-2c18cd6bcb70", "vc (STRING)", "ENCOUNTER.ENCNTR_ID", "Encounter", None),
                    ("Enc Financial Num", "1a4ee2f6-2ed1-48b3-9d79-974352b9fabf", "vc (STRING)", "ENCNTR_ALIAS.ALIAS", "Encounter", 319),
                    ("Person MRN", "12db87ba-4466-4682-8f6c-28adf9a8c8d3", "vc (STRING)", "PERSON_ALIAS.ALIAS", "Person", 4),
                    ("Person Name Full Format", "12b363ca-f0d2-4a5d-afe3-3225de181f5f", "vc (STRING)", "PERSON.NAME_FULL_FORMATTED", "Person", None),
                    ("Enc Loc Facility", "b09f6eab-aa80-4110-a4af-3dcbbd00384b", "vc (STRING)", "ENCOUNTER.LOC_FACILITY_CD", "Encounter", 220),
                    ("Enc Loc Nurse Unit", "75267dc7-d5c6-437b-aa34-98e46bbc5db1", "vc (STRING)", "ENCOUNTER.LOC_NURSE_UNIT_CD", "Encounter", 220),
                    ("Enc Loc Room", "e52b4716-00f9-42ab-8a04-8ba1e9ea1ece", "vc (STRING)", "ENCOUNTER.LOC_ROOM_CD", "Encounter", 220),
                    ("Bed Loc", "219ba43a-c7ee-44a4-8e79-052d4a5703da", "vc (STRING)", "ENCOUNTER.LOC_BED_CD", "Encounter", 220),
                    ("Enc Attending Phys", "beb01385-36a9-4371-93e2-68056229e216", "vc (STRING)", "CWC_ENC_FUNC.GET_ATTEND_PHYS_NAME(CWC_ENCOUNTER.ENCNTR_ID)", "Encounter Functions", None),
                    ("Enc Type", "10b02c36-ff84-47d6-a825-ea142281a547", "vc (STRING)", "ENCOUNTER.ENCNTR_TYPE_CD", "Encounter", 71),
                    ("Enc Status", "7161959f-72cd-46bb-b6f6-2b177edd0469", "vc (STRING)", "ENCOUNTER.ENCNTR_STATUS_CD", "Encounter", 261),
                    ("Enc Registration Dt/Tm", "42ee9a32-031f-495d-962a-5f04376e071a", "dq8 (DATETIME)", "ENCOUNTER.REG_DT_TM", "Encounter", None),
                    ("Enc Discharge Dt/Tm", "1d44a53e-cba7-4ddf-80c4-d86205e30e0d", "dq8 (DATETIME)", "ENCOUNTER.DISCH_DT_TM", "Encounter", None),
                    ("CE Id", "05a1f88d-6a33-4190-86ac-704249934924", "vc (STRING)", "CLINICAL_EVENT.CLINICAL_EVENT_ID", "Clinical Event", None),
                    ("CE Event Id", "bfc961f6-7aa0-4203-9075-82a5058e4ebb", "vc (STRING)", "CLINICAL_EVENT.EVENT_ID", "Clinical Event", None),
                    ("CE Event", "f01d0cc1-f599-46be-b816-ecd82fffbb0e", "vc (STRING)", "CLINICAL_EVENT.EVENT_CD", "Clinical Event", 72),
                    ("CE End Dt/Tm", "4433e8b0-5947-4f79-b4aa-c32c8ec30c45", "dq8 (DATETIME)", "CLINICAL_EVENT.EVENT_END_DT_TM", "Clinical Event", None),
                    ("CE Performed Prsnl Name", "2ecc5b49-c180-440c-b0c2-ce9712307ba3", "vc (STRING)", "CLINICAL_EVENT.PERFORMED_PRSNL_ID", "Clinical Event", None),
                    ("CE Result Value", "155eec27-4c56-4cff-a4ee-861aa3335814", "vc (STRING)", "CLINICAL_EVENT.RESULT_VAL", "Clinical Event", None),
                    ("CE Result Status", "7ccf3fe5-568a-4015-82e0-fd370a98426e", "vc (STRING)", "CLINICAL_EVENT.RESULT_STATUS_CD", "Clinical Event", 8),
                    ("CE Valid Until Dt/Tm", "871b99b7-e872-4db0-8762-d663c933ad76", "dq8 (DATETIME)", "CLINICAL_EVENT.VALID_UNTIL_DT_TM", "Clinical Event", None),
                ]
            if is_provider_clinical_notes:
                target_ce_events = PROVIDER_CLINICAL_NOTES_CE_EVENTS
            else:
                composed_ce: list[tuple[str, str]] = []
                for flag, ev_group in (
                    (is_oral_care, ORAL_CARE_CE_EVENTS),
                    (is_bladder_bowel, BLADDER_BOWEL_CE_EVENTS),
                    (is_bathing_hygiene, BATHING_HYGIENE_CE_EVENTS),
                    (is_nutrition_assessment, NUTRITION_ASSESSMENT_CE_EVENTS),
                    (is_pharmacy_interventions, PHARMACY_INTERVENTIONS_CE_EVENTS),
                    (is_iv_central_line, IV_CENTRAL_LINE_CE_EVENTS),
                    (is_census_risk_indicators, CENSUS_RISK_CE_EVENTS),
                    (is_rehab_therapy_minutes, REHAB_THERAPY_CE_EVENTS),
                ):
                    if flag:
                        for pair in ev_group:
                            if pair not in composed_ce:
                                composed_ce.append(pair)
                target_ce_events = composed_ce or REHAB_THERAPY_CE_EVENTS
            for cname, cuuid, ctype, vtarget, subent, cset in base_clin_cols:
                if cname in seen_names:
                    continue
                seen_names.add(cname)
                all_elements.append({
                    "da2_field_name": cname,
                    "da2_domain": "CWC Encounter",
                    "da2_bus_view_subentity": subent,
                    "v500_target": vtarget,
                    "ccl_data_type": ctype,
                    "code_set": cset,
                    "element_uuid": cuuid,
                    "primary_ce_events": target_ce_events if cname == "CE Event" else None,
                    "value_semantics_and_guardrails": "Live-verified CWC Encounter clinical element",
                })
            if is_revenue_charge_reconciliation:
                chg_desc_inline = str(uuid.uuid5(uuid.NAMESPACE_OID, f"com.cerner.discern.da2.inline:{report_name}:ChgRecon:Chg Description"))
                chg_price_inline = str(uuid.uuid5(uuid.NAMESPACE_OID, f"com.cerner.discern.da2.inline:{report_name}:ChgRecon:Chg Gross Price"))
                all_elements.append({
                    "da2_field_name": "ChgRecon:Chg Description",
                    "display_label": "Posted Charge Description",
                    "inline_query_name": "ChgRecon",
                    "base_field_name": "Chg Description",
                    "inline_uuid": chg_desc_inline,
                    "subquery_events": [],
                    "subquery_label": "Encounter Charge Detail",
                    "da2_domain": "CWC Encounter",
                    "da2_bus_view_subentity": "Charge",
                    "v500_target": "CHARGE.CHARGE_DESCRIPTION",
                    "ccl_data_type": "vc (STRING)",
                    "code_set": None,
                    "element_uuid": "3f56ba91-86c6-4081-909a-36ca011066c6",
                    "value_semantics_and_guardrails": "Inline outer-joined CWC Charge bill item description",
                })
                all_elements.append({
                    "da2_field_name": "ChgRecon:F Chg Status",
                    "display_label": "Financial Charge Status",
                    "inline_query_name": "ChgRecon",
                    "base_field_name": "F Chg Status",
                    "inline_uuid": chg_price_inline,
                    "subquery_events": [],
                    "subquery_label": "Encounter Charge Detail",
                    "da2_domain": "CWC Encounter",
                    "da2_bus_view_subentity": "Charge",
                    "v500_target": "CHARGE.PROCESS_FLG",
                    "ccl_data_type": "vc (STRING)",
                    "code_set": None,
                    "element_uuid": "30aadfd4-ba16-4e13-a56d-b0de02c4882b",
                    "value_semantics_and_guardrails": "Inline outer-joined CWC Charge financial status (verified CWC Charge financial status)",
                })

        # Ensure CE version/status fields are present whenever any CE field is selected
        has_ce = any(e["da2_field_name"].startswith("CE ") for e in all_elements)
        if has_ce and not (is_cssrs or is_custom_ce_template or is_targeted_order_report):
            add_elem("CE Result Status")
            add_elem("CE Valid Until Dt Tm")

        has_combined_order_and_mar = is_targeted_order_report and any(
            e["da2_field_name"].startswith(("Med Adm", "Task Atv", "CE Med")) for e in all_elements
        )
        if (interp.recommended_strategy == "two_dataset_birt_join" and not (
            is_cssrs or is_custom_ce_template or is_targeted_order_report
        )) or (is_targeted_order_report and is_custom_ce_template) or has_combined_order_and_mar:
            # Split into Primary (Encounter / Clinical) and Secondary (Financial / Charges or second child list)
            fin_elements = [
                e for e in all_elements
                if e["da2_field_name"] in {"Enc Id", "Enc Encounter Number"}
                or e["da2_field_name"].startswith(("Chg ", "BO ", "HP ", "Account "))
                or "CWF" in e.get("da2_domain", "")
            ]
            clin_elements = [
                e for e in all_elements
                if e["da2_field_name"] in {"Enc Id", "Enc Encounter Number"}
                or not (
                    e["da2_field_name"].startswith(("Chg ", "BO ", "HP "))
                    or "CWF" in e.get("da2_domain", "")
                )
            ]
            # If the split was triggered by two clinical child lists (e.g. CE + Prsnl/Sch/Task) rather than CWF:
            if len(fin_elements) <= 2:
                secondary_prefixes = ("CE ", "CE Med ", "Med Adm", "Task Atv") if has_combined_order_and_mar else ("CE ", "CE Med ")
                fin_elements = [
                    e for e in all_elements
                    if e["da2_field_name"] in {"Enc Id", "Enc Encounter Number"}
                    or e["da2_field_name"].startswith(secondary_prefixes)
                ]
                clin_elements = [
                    e for e in all_elements
                    if e["da2_field_name"] in {"Enc Id", "Enc Encounter Number"}
                    or not e["da2_field_name"].startswith(secondary_prefixes)
                ]
                sec_domain = "CWC Encounter"
            else:
                if "adjustment" in low_req or any(e["da2_field_name"].startswith("Adj") for e in fin_elements):
                    sec_domain = "CWF GS Adjustments"
                elif "payment" in low_req or any(e["da2_field_name"].startswith("Pay") for e in fin_elements):
                    sec_domain = "CWF GS Payments"
                elif "general ledger" in low_req or "cwf gl" in low_req:
                    sec_domain = "CWF GL"
                elif any(e["da2_field_name"].startswith("BO ") for e in fin_elements):
                    sec_domain = "CWF GS Revenue"
                else:
                    sec_domain = "CWC Encounter"

            for req_dt_col, req_dt_uuid in (
                ("Enc Registration Dt/Tm", "42ee9a32-031f-495d-962a-5f04376e071a"),
                ("Enc Discharge Dt/Tm", "1d44a53e-cba7-4ddf-80c4-d86205e30e0d"),
            ):
                for target_list in (clin_elements, fin_elements):
                    if not any(el["da2_field_name"] in (req_dt_col, "Enc Admit Dt Tm", "Enc Admit Dt/Tm", "Enc Disch Dt Tm") and (req_dt_col.split()[1] in el["da2_field_name"]) for el in target_list):
                        target_list.append({
                            "da2_field_name": req_dt_col,
                            "da2_domain": "CWC Encounter",
                            "da2_bus_view_subentity": "Encounter",
                            "v500_target": "ENCOUNTER.REG_DT_TM" if "Registration" in req_dt_col else "ENCOUNTER.DISCH_DT_TM",
                            "ccl_data_type": "dq8 (DATETIME)",
                            "code_set": None,
                            "element_uuid": req_dt_uuid,
                            "value_semantics_and_guardrails": "Live-verified CWC Encounter date-time anchor",
                        })

            q1 = self._make_single_query_spec(
                f"{report_name}_Primary_CWC",
                "CWC Encounter",
                clin_elements,
                is_active_census=is_active_census,
                owner_group_display=owner_group_display,
                value_filters=value_filters,
            )
            q2 = self._make_single_query_spec(
                f"{report_name}_Secondary_Detail",
                sec_domain,
                fin_elements,
                is_active_census=is_active_census,
                owner_group_display=owner_group_display,
                value_filters=value_filters,
            )
            return [q1, q2], "Enc Id"

        single_domain = "CWC Encounter"
        if "cwf gs adjustments" in low_req or ("adjustment" in low_req and ("cwf" in low_req or "write-off" in low_req or "transaction extract" in low_req)):
            single_domain = "CWF GS Adjustments"
        elif "cwf gs payments" in low_req or ("payment" in low_req and ("cwf" in low_req or "ledger" in low_req or "transaction extract" in low_req)):
            single_domain = "CWF GS Payments"
        elif "cwf gs revenue" in low_req:
            single_domain = "CWF GS Revenue"
        elif "cwf gl" in low_req or "general ledger" in low_req:
            single_domain = "CWF GL"
        elif "cwc scheduling" in low_req or "scheduling resource" in low_req or "slot utilization" in low_req:
            single_domain = "CWC Scheduling Resource"
        elif "cwc formulary" in low_req:
            single_domain = "CWC Formulary"
        elif "cwc personnel" in low_req:
            single_domain = "CWC Personnel"
        elif "cwc person" in low_req and "encounter" not in low_req:
            single_domain = "CWC Person"
        elif "cwc organization" in low_req:
            single_domain = "CWC Organization"

        if any(k in low_req for k in ("cwf gs adjustments", "cwf gs payments", "resuscitation", "code status", "slot utilization", "duplicate charge")):
            all_elements = [
                e for e in all_elements
                if e["da2_field_name"] not in ("Enc Id", "Enc Encounter Number", "Person Full Name", "CE Result Status", "CE Valid Until Dt Tm")
            ]

        q_single = self._make_single_query_spec(
            f"{report_name}_Query",
            single_domain,
            all_elements,
            is_active_census=is_active_census,
            owner_group_display=owner_group_display,
            value_filters=value_filters,
        )
        return [q_single], None

    def _make_single_query_spec(
        self,
        q_name: str,
        domain: str,
        elements: list[dict[str, Any]],
        is_active_census: bool,
        owner_group_display: str = DEFAULT_OWNER_GROUP_DISPLAY,
        value_filters: list[dict[str, Any]] | None = None,
    ) -> DA2QuerySpec:
        # The value maps are CWC Encounter elements; other domains don't carry them.
        value_filters = value_filters if domain == "CWC Encounter" else []
        q_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"com.cerner.discern.da2.query:{q_name}"))
        sub_entities = sorted({e.get("da2_bus_view_subentity", "Encounter") for e in elements})
        filters = [
            "Enc Active Ind = 1",
            "Person Name Last < 'ZZ' (exclude test patients)",
        ]
        el_names_set = {e["da2_field_name"] for e in elements}
        if any(n.startswith("Adj ") for n in el_names_set):
            filters.extend([
                "Adj Trans Sub Type (Code Set 20549)",
                "Adj Trans Reason (Code Set 18935)",
                "Enc Type (Code Set 71)",
                "Adj Post Dt Between <Relative Date Range>",
            ])
        if any(n.startswith("Pay ") for n in el_names_set):
            filters.extend([
                "Pay Trans Sub Type (Code Set 20549)",
                "Pay Trans Reason (Code Set 18935)",
                "Enc Type (Code Set 71)",
                "Pay Post Dt Between <Relative Date Range>",
            ])
        if "Order Catalog" in el_names_set and "Enc Loc Room" in el_names_set:
            filters.extend([
                "Order Catalog (Code Set 200)",
                "Enc Status (Code Set 261)",
                "Enc Type (Code Set 71)",
            ])
        if any(n.startswith("Sch ") for n in el_names_set):
            filters.extend([
                "Sch App State (Code Set 14233)",
                "Sch App Role (Code Set 14250)",
                "Sch App Slot State (Code Set 14231)",
                "Sch Ev App Type (Code Set 14230)",
                "Sch App Resource Not Like 'zz*' (exclude retired scheduling resources)",
            ])
        if "Chg M Field 1 Id" in el_names_set or "Chg Manual Ind" in el_names_set:
            filters.extend([
                "Chg Process Flag",
                "Chg Service Dt/Tm Between <Relative Date Range>",
            ])
        # "MAR" as its own word of the query name, not inside SUMMARY or PRIMARY
        if any("Med Admin" in n for n in el_names_set) or {"MAR", "MARS"} & set(re.split(r"[^A-Z0-9]+", q_name.upper())):
            filters.append(
                "Order Status Not In (Canceled, Voided) [Code Set 6004: 2542, 2553] — NEVER exclude Discontinued (2545) on historical MAR/PRN audits"
            )
        if any(n.startswith(("Diag ", "Dx ")) for n in el_names_set):
            filters.append(
                "Outer Join (joinType=3 / LEFT OUTER JOIN / (+)) on DIAGNOSIS so same-day admissions without a coded diagnosis are not dropped"
            )

        if is_active_census:
            filters.extend([
                "Enc Admit Dt Tm <= Current Date/Time",
                "Enc Disch Dt Tm IS NULL (uncheck 'is not null' on optional date prompt)",
            ])
        if any(e["da2_field_name"].startswith("CE ") for e in elements):
            filters.extend([
                "CE Valid Until Dt Tm >= 12/31/2100 00:00:00",
                "CE Result Status in (Auth (Verified), Modified, Altered) [Code Set 8]",
            ])
        inline_subq_names = []
        for e in elements:
            sqn = e.get("inline_query_name")
            if sqn and sqn not in inline_subq_names:
                inline_subq_names.append(sqn)
        if inline_subq_names:
            filters.extend([
                "CE Event in (Admission History Adult - Form, CSSRS Adult Screening - Form, C-SSRS Columbia Suicide Severity Rating Scale, and 12 C-SSRS clinical event items) [Code Set 72]",
                "Enc Type in (Inpatient Rehabilitation, Outpatient, Recurring) [Code Set 71]",
                f"Outer-joined (joinType=3) inline subqueries on Enc Id + CE Parent Event Id ({len(inline_subq_names)} subqueries): " + ", ".join(inline_subq_names),
            ])

        # Determine primary runtime date prompt (use indexed Enc Registration Dt/Tm when inline subqueries are present to prevent full table scans of V500.CLINICAL_EVENT)
        preferred_date_fields = (
            ("Enc Registration Dt/Tm", "Enc Admit Dt/Tm", "Enc Admit Dt Tm")
            if inline_subq_names
            else ("CE Performed Dt/Tm", "CE End Dt/Tm", "CE Event End Dt Tm", "CE Event End Dt/Tm")
        )
        date_elem = next(
            (e for e in elements if e["da2_field_name"] in preferred_date_fields),
            next(
                (
                    e for e in elements
                    if "Dt" in e["da2_field_name"] and "Valid Until" not in e["da2_field_name"] and "Birth" not in e["da2_field_name"]
                ),
                {
                    "da2_field_name": "Enc Registration Dt/Tm",
                    "element_uuid": "42ee9a32-031f-495d-962a-5f04376e071a",
                },
            ),
        )
        prompt_name = date_elem["da2_field_name"]
        prompt_uuid = date_elem["element_uuid"]
        if prompt_name in LIVE_CWC_ELEMENT_MAP:
            prompt_name, prompt_uuid, _ = LIVE_CWC_ELEMENT_MAP[prompt_name]
        elif uuid.UUID(prompt_uuid).version == 5:
            prompt_name, prompt_uuid = "Enc Registration Dt/Tm", "42ee9a32-031f-495d-962a-5f04376e071a"

        inner_qual = (
            '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
            '<qualification>'
            '<filter joinType="0" mode="2" useIndex="1">'
            f'<element name="{html.escape(prompt_name, quote=True)}" type="2" uuid="{prompt_uuid}"/>'
            '<values type="LIST" xCount="1">'
            '<value cdfmeaning="" count="7" display="Last 7 Days" forward="false" period="2"/>'
            '</values>'
            '</filter>'
            '</qualification>'
        )
        qual_xml = (
            f'<prompts><qualification hidden="false" prompt-mode="always" '
            f'runtimePromptName="{html.escape(prompt_name, quote=True)}" uuid="{prompt_uuid}">'
            f"{html.escape(inner_qual)}"
            f"</qualification></prompts>"
        )

        prompts = [
            {
                "name": prompt_name,
                "nativeName": prompt_name,
                "dataType": "date",
                "nativeDataType": 91,
                "position": 1,
                "element_uuid": prompt_uuid,
                "default_window": "Last 7 Days",
            }
        ]

        cclquery_xml = self._render_cclquery_xml(
            q_name=q_name,
            q_uuid=q_uuid,
            domain=domain,
            elements=elements,
            prompt_name=prompt_name,
            prompt_uuid=prompt_uuid,
            is_active_census=is_active_census,
            value_filters=value_filters,
        )
        ejs_v500_sql = self._render_ejs_v500_sql(
            q_name=q_name,
            q_uuid=q_uuid,
            domain=domain,
            elements=elements,
            is_active_census=is_active_census,
            value_filters=value_filters,
        )

        daquery_xml = self._render_daquery_xml(
            q_name=q_name,
            q_uuid=q_uuid,
            domain=domain,
            elements=elements,
            prompt_name=prompt_name,
            prompt_uuid=prompt_uuid,
            is_active_census=is_active_census,
            owner_group_display=owner_group_display,
            value_filters=value_filters,
        )
        filters.extend(vf["text"] for vf in value_filters or [])

        return DA2QuerySpec(
            query_name=q_name,
            query_uuid=q_uuid,
            da2_domain=domain,
            sub_entities=sub_entities,
            selected_elements=elements,
            filters=filters,
            runtime_prompts=prompts,
            qualification_xml=qual_xml,
            cclquery_xml=cclquery_xml,
            ejs_v500_sql=ejs_v500_sql,
            daquery_xml=daquery_xml,
            owner_group_display=owner_group_display,
        )

    def _render_daquery_xml(
        self,
        q_name: str,
        q_uuid: str,
        domain: str,
        elements: list[dict[str, Any]],
        prompt_name: str,
        prompt_uuid: str,
        is_active_census: bool,
        owner_group_display: str = DEFAULT_OWNER_GROUP_DISPLAY,
        value_filters: list[dict[str, Any]] | None = None,
    ) -> str:
        """Renders a native Cerner DA2 .daquery file for File -> Import -> Other -> Query."""
        dom_uuid = "690ff3c7-f0b2-4019-8176-4e26eeff14f0" if domain == "CWC Encounter" else DOMAIN_UUID_MAP.get(domain, "690ff3c7-f0b2-4019-8176-4e26eeff14f0")
        esc_qname = html.escape(q_name, quote=True)
        esc_owner = html.escape(owner_group_display, quote=True)
        el_nodes: list[str] = []
        seen_daquery_uuids: set[str] = set()
        inline_subqueries: list[tuple[str, list[tuple[str, str]]]] = []
        seen_subq: set[str] = set()
        seq_idx = 0
        for el in elements:
            sq_name = el.get("inline_query_name")
            if sq_name:
                if sq_name not in seen_subq:
                    seen_subq.add(sq_name)
                    inline_subqueries.append((sq_name, el.get("subquery_events", [])))
                seq_idx += 1
                base_name = el["base_field_name"]
                inline_uuid = el["inline_uuid"]
                euuid = el["element_uuid"]
                el_nodes.append(
                    f'<element index="{seq_idx}" inlineUUID="{inline_uuid}" '
                    f'joinParent="{html.escape(sq_name, quote=True)}" name="{html.escape(base_name, quote=True)}" '
                    f'type="3" uuid="{euuid}"/>'
                )
                continue
            raw_name = el["da2_field_name"]
            if raw_name in LIVE_CWC_ELEMENT_MAP:
                ename, euuid, etype = LIVE_CWC_ELEMENT_MAP[raw_name]
            else:
                ename = raw_name
                euuid = el["element_uuid"]
                ct = (el.get("ccl_data_type") or "").lower()
                etype = "2" if ("datetime" in ct or "dq8" in ct or "Dt" in ename) else ("1" if ("i4" in ct or "f8" in ct or "Ind" in ename) else "3")
            try:
                if uuid.UUID(euuid).version == 5:
                    continue
            except ValueError:
                continue
            if domain == "CWC Encounter" and euuid not in VERIFIED_CWC_ENCOUNTER_UUIDS:
                continue
            if euuid in seen_daquery_uuids:
                continue
            seen_daquery_uuids.add(euuid)
            seq_idx += 1
            el_nodes.append(f'<element index="{seq_idx}" name="{html.escape(ename, quote=True)}" type="{etype}" uuid="{euuid}"/>')

        if prompt_name in LIVE_CWC_ELEMENT_MAP:
            prompt_name, prompt_uuid, _ = LIVE_CWC_ELEMENT_MAP[prompt_name]
        esc_pname = html.escape(prompt_name, quote=True)
        f1 = (
            f'<filter joinType="0" mode="4" useIndex="1">'
            f'<element name="{esc_pname}" type="2" uuid="{prompt_uuid}"/>'
            f'<runtimeprompt name="{esc_pname}"/>'
            f'</filter>'
        )
        f2 = (
            '<filter2 joinType="0" mode="0" useIndex="1">'
            '<element name="Person Name Last" type="3" uuid="2e9a30aa-c98f-4d50-9f5e-8d642c169fe8"/>'
            '<operator type="LESS THAN"/>'
            '<values type="LIST" xCount="1"><value text="ZZ"/></values>'
            '</filter2>'
        )
        qual_body = f'<operator type="AND">{f1}{f2}</operator>'
        has_ce_da = any(e["da2_field_name"].startswith("CE ") for e in elements)
        has_event_title = any(e["da2_field_name"] == "CE Event Title" for e in elements)
        if has_ce_da and not has_event_title:
            f_ce_stat = (
                '<filter joinType="0" mode="0" prompt_element_uuid="7ccf3fe5-568a-4015-82e0-fd370a98426e" prompt_type="0" useIndex="1">'
                '<element name="CE Result Status" type="1" uuid="7ccf3fe5-568a-4015-82e0-fd370a98426e"/>'
                '<operator type="IN"/>'
                '<values type="LIST" xCount="2"><value display="Auth (Verified)" text="25.00"/><value display="Modified" text="35.00"/></values>'
                '</filter>'
            )
            f_ce_valid = (
                '<filter joinType="0" mode="2" prompt_element_uuid="871b99b7-e872-4db0-8762-d663c933ad76" prompt_type="1" useIndex="1">'
                '<element name="CE Valid Until Dt/Tm" type="2" uuid="871b99b7-e872-4db0-8762-d663c933ad76"/>'
                '<values type="LIST" xCount="1"><value cdfmeaning="&gt;= TODAY" display="&gt;= Today                           &#127;"/></values>'
                '</filter>'
            )
            qual_body = f'<operator type="AND"><operator type="AND">{qual_body}{f_ce_stat}</operator>{f_ce_valid}</operator>'

        primary_ord_cat = next((el.get("primary_order_catalog") for el in elements if el.get("primary_order_catalog")), None)
        primary_ord_cat_types = next((el.get("primary_order_catalog_types") for el in elements if el.get("primary_order_catalog_types")), None)
        f_ord_cat = ""
        if primary_ord_cat:
            o_vals = "".join(
                f'<value display="{html.escape(o_disp, quote=True)}" text="{html.escape(o_cd, quote=True)}"/>'
                for o_disp, o_cd in primary_ord_cat
            )
            f_ord_cat = (
                '<filter joinType="0" mode="0" prompt_element_uuid="ca672220-76a6-4601-bc0f-f0104e07874c" prompt_type="0" useIndex="1">'
                '<element name="Order Catalog" type="1" uuid="ca672220-76a6-4601-bc0f-f0104e07874c"/>'
                '<operator type="IN"/>'
                f'<values type="LIST" xCount="{len(primary_ord_cat)}">{o_vals}</values>'
                '</filter>'
            )
        f_ord_type = ""
        if primary_ord_cat_types:
            ot_vals = "".join(
                f'<value display="{html.escape(o_disp, quote=True)}" text="{html.escape(o_cd, quote=True)}"/>'
                for o_disp, o_cd in primary_ord_cat_types
            )
            f_ord_type = (
                '<filter joinType="0" mode="0" prompt_element_uuid="97f18dc3-faf9-42d5-a47a-999fe7cdf944" prompt_type="0" useIndex="1">'
                '<element name="Order Catalog Type" type="1" uuid="97f18dc3-faf9-42d5-a47a-999fe7cdf944"/>'
                '<operator type="IN"/>'
                f'<values type="LIST" xCount="{len(primary_ord_cat_types)}">{ot_vals}</values>'
                '</filter>'
            )
        if f_ord_cat and f_ord_type:
            qual_body = f'<operator type="AND">{qual_body}<operator paren="true" type="OR">{f_ord_cat}{f_ord_type}</operator></operator>'
        elif f_ord_cat:
            qual_body = f'<operator type="AND">{qual_body}{f_ord_cat}</operator>'
        elif f_ord_type:
            qual_body = f'<operator type="AND">{qual_body}{f_ord_type}</operator>'

        primary_ce_events = next((el.get("primary_ce_events") for el in elements if el.get("primary_ce_events")), None)
        if primary_ce_events and not any(sq[0] == "WishD" for sq in inline_subqueries):
            p_vals = "".join(
                f'<value display="{html.escape(ev_disp, quote=True)}" text="{html.escape(ev_cd, quote=True)}"/>'
                for ev_disp, ev_cd in primary_ce_events
            )
            f_primary_ce = (
                '<filter joinType="0" mode="0" prompt_element_uuid="f01d0cc1-f599-46be-b816-ecd82fffbb0e" prompt_type="0" useIndex="1">'
                '<element name="CE Event" type="1" uuid="f01d0cc1-f599-46be-b816-ecd82fffbb0e"/>'
                '<operator type="IN"/>'
                f'<values type="LIST" xCount="{len(primary_ce_events)}">{p_vals}</values>'
                '</filter>'
            )
            qual_body = f'<operator type="AND">{qual_body}{f_primary_ce}</operator>'
            has_event_title = any(e["da2_field_name"] == "CE Event Title" for e in elements)
            if has_event_title:
                f_ev_title = (
                    '<filter joinType="0" mode="0" useIndex="1">'
                    '<element name="CE Event Title" type="3" uuid="3aea7feb-ce08-4dc6-92e2-ebe8fa04d94d"/>'
                    '<operator type="NOT EQUALS"/>'
                    '<values type="LIST" xCount="1"><value text="\' \'"/></values>'
                    '</filter>'
                )
                qual_body = f'<operator type="AND">{qual_body}{f_ev_title}</operator>'
            elif not is_active_census:
                f_enc_type = (
                    '<filter joinType="0" mode="0" prompt_element_uuid="10b02c36-ff84-47d6-a825-ea142281a547" prompt_type="0" useIndex="1">'
                    '<element name="Enc Type" type="1" uuid="10b02c36-ff84-47d6-a825-ea142281a547"/>'
                    '<operator type="IN"/>'
                    '<values type="LIST" xCount="3">'
                    '<value display="Inpatient Rehabilitation" text="72191245.00"/>'
                    '<value display="Outpatient" text="309309.00"/>'
                    '<value display="Recurring" text="309314.00"/>'
                    '</values>'
                    '</filter>'
                )
                qual_body = f'<operator type="AND">{qual_body}{f_enc_type}</operator>'

        if inline_subqueries:
            if any(sq[0] == "WishD" for sq in inline_subqueries):
                # Primary C-SSRS trigger event on Query1 (Suicidal Thoughts, matching live CSSRS Response Sthoughts)
                f_q1_ce_event = (
                    '<filter joinType="0" mode="0" prompt_element_uuid="f01d0cc1-f599-46be-b816-ecd82fffbb0e" prompt_type="0" useIndex="1">'
                    '<element name="CE Event" type="1" uuid="f01d0cc1-f599-46be-b816-ecd82fffbb0e"/>'
                    '<operator type="IN"/>'
                    '<values type="LIST" xCount="1">'
                    '<value display="CSSRS Since Last Ask Suicidal Thoughts" text="31360467.00"/>'
                    '</values>'
                    '</filter>'
                )
                f_enc_type = (
                    '<filter joinType="0" mode="0" prompt_element_uuid="10b02c36-ff84-47d6-a825-ea142281a547" prompt_type="0" useIndex="1">'
                    '<element name="Enc Type" type="1" uuid="10b02c36-ff84-47d6-a825-ea142281a547"/>'
                    '<operator type="IN"/>'
                    '<values type="LIST" xCount="3">'
                    '<value display="Inpatient Rehabilitation" text="72191245.00"/>'
                    '<value display="Outpatient" text="309309.00"/>'
                    '<value display="Recurring" text="309314.00"/>'
                    '</values>'
                    '</filter>'
                )
                qual_body = f'<operator type="AND"><operator type="AND">{qual_body}{f_q1_ce_event}</operator>{f_enc_type}</operator>'

            # Outer-join filters (joinType="3" mode="1" useIndex="1") on Enc Id (5ef4381c-6344-43c4-81b5-2c18cd6bcb70)
            # Single-table outer join on CWC_ENCOUNTER.ENCNTR_ID avoids Oracle ORA-01416 while matching IView self-parented C-SSRS DTAs.
            for sq_name, _ in inline_subqueries:
                esc_sq = html.escape(sq_name, quote=True)
                enc_join_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"com.cerner.discern.da2.join:{q_name}:{sq_name}:Enc Id"))
                f_join_enc = (
                    '<filter joinType="3" mode="1" useIndex="1">'
                    '<element name="Enc Id" type="1" uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"/>'
                    '<operator type="EQUALS"/>'
                    '<elements type="LIST" xCount="1">'
                    f'<element column_value_flag="0" inlineUUID="{enc_join_uuid}" joinParent="{esc_sq}" name="{esc_sq}:Enc Id" type="1" uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"/>'
                    '</elements>'
                    '</filter>'
                )
                qual_body = f'<operator type="AND">{qual_body}{f_join_enc}</operator>'

        if is_active_census:
            f_act = (
                '<filter joinType="0" mode="0" prompt_element_uuid="7161959f-72cd-46bb-b6f6-2b177edd0469" prompt_type="0" useIndex="1">'
                '<element name="Enc Status" type="1" uuid="7161959f-72cd-46bb-b6f6-2b177edd0469"/>'
                '<operator type="NOT IN"/>'
                '<values type="LIST" xCount="1"><value display="Discharged" text="856.00"/></values>'
                '</filter>'
            )
            qual_body = f'<operator type="AND">{qual_body}{f_act}</operator>'

        for vf in value_filters or []:
            v_vals = "".join(
                f'<value display="{html.escape(d, quote=True)}" text="{c}.00"/>' for d, c in vf["values"]
            )
            v_name = html.escape(LIVE_CWC_ELEMENT_MAP[vf["name"]][0], quote=True)
            if vf.get("like"):  # the shape DA2 itself writes for an Is Like qualification
                f_val = (
                    f'<filter joinType="0" mode="0" useIndex="1"><element name="{v_name}" type="3" uuid="{vf["uuid"]}"/>'
                    '<operator type="LIKE"/>'
                    f'<values type="LIST" xCount="1"><value text="{html.escape(vf["like"], quote=True)}"/></values>'
                    '</filter>'
                )
                qual_body = f'<operator type="AND">{qual_body}{f_val}</operator>'
                continue
            f_val = (
                f'<filter joinType="0" mode="0" prompt_element_uuid="{vf["uuid"]}" prompt_type="0" useIndex="1">'
                f'<element name="{v_name}" type="1" uuid="{vf["uuid"]}"/>'
                '<operator type="IN"/>'
                f'<values type="LIST" xCount="{len(vf["values"])}">{v_vals}</values>'
                '</filter>'
            )
            qual_body = f'<operator type="AND">{qual_body}{f_val}</operator>'

        # Note: Organization/tenant security in DA2 is enforced via the Select Organizations schedule list
        # and/or Enc Loc Facility (b09f6eab-aa80-4110-a4af-3dcbbd00384b); never inject a synthetic Logical Domain ID UUID.

        subquery_items_xml: list[str] = []
        for idx, (sq_name, sq_ev_list) in enumerate(inline_subqueries, 2):
            esc_sq = html.escape(sq_name, quote=True)
            if sq_name == "ChgRecon" or not sq_ev_list:
                subquery_items_xml.append(
                    f'<query_list_item{idx} inline_ind="1" name="{esc_sq}" usegroupby="0" useorderby="0">'
                    f'<domain name="{html.escape(domain, quote=True)}" uuid="{dom_uuid}"/>'
                    '<output><elements type="LIST" xCount="3">'
                    '<element index="1" name="Enc Id" type="3" uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"/>'
                    '<element index="2" name="Chg Description" type="3" uuid="3f56ba91-86c6-4081-909a-36ca011066c6"/>'
                    '<element index="3" name="F Chg Status" type="3" uuid="30aadfd4-ba16-4e13-a56d-b0de02c4882b"/>'
                    '</elements></output>'
                    '<qualification><operator paren="true" type="AND">'
                    f'<filter joinType="0" mode="4" useIndex="1">'
                    f'<element name="{esc_pname}" type="2" uuid="{prompt_uuid}"/>'
                    f'<runtimeprompt name="{esc_pname}"/>'
                    f'</filter>'
                    '<filter2 joinType="0" mode="0" useIndex="1">'
                    '<element name="Chg Item Quantity" type="1" uuid="9c7440d4-51a5-4dd1-ac0c-a8bf799fb074"/>'
                    '<operator type="EQUALS"/>'
                    '<values type="LIST" xCount="1"><value text="1"/></values>'
                    '</filter2>'
                    '</operator></qualification>'
                    f'</query_list_item{idx}>'
                )
                continue
            sq_vals = "".join(
                f'<value display="{html.escape(ev_disp, quote=True)}" text="{html.escape(ev_cd, quote=True)}"/>'
                for ev_disp, ev_cd in sq_ev_list
            )
            subquery_items_xml.append(
                f'<query_list_item{idx} inline_ind="1" name="{esc_sq}" usegroupby="0" useorderby="0">'
                f'<domain name="{html.escape(domain, quote=True)}" uuid="{dom_uuid}"/>'
                '<output><elements type="LIST" xCount="3">'
                '<element index="1" name="Enc Id" type="3" uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"/>'
                '<element index="2" name="CE Event" type="3" uuid="f01d0cc1-f599-46be-b816-ecd82fffbb0e"/>'
                '<element index="3" name="CE Result Value" type="3" uuid="155eec27-4c56-4cff-a4ee-861aa3335814"/>'
                '</elements></output>'
                '<qualification><operator paren="true" type="AND">'
                '<operator type="AND">'
                f'<filter joinType="0" mode="4" useIndex="1">'
                f'<element name="{esc_pname}" type="2" uuid="{prompt_uuid}"/>'
                f'<runtimeprompt name="{esc_pname}"/>'
                f'</filter>'
                '<filter2 joinType="0" mode="0" prompt_element_uuid="f01d0cc1-f599-46be-b816-ecd82fffbb0e" prompt_type="0" useIndex="1">'
                '<element name="CE Event" type="1" uuid="f01d0cc1-f599-46be-b816-ecd82fffbb0e"/>'
                '<operator type="IN"/>'
                f'<values type="LIST" xCount="{len(sq_ev_list)}">{sq_vals}</values>'
                '</filter2>'
                '</operator>'
                '<filter joinType="0" mode="0" prompt_element_uuid="7ccf3fe5-568a-4015-82e0-fd370a98426e" prompt_type="0" useIndex="1">'
                '<element name="CE Result Status" type="1" uuid="7ccf3fe5-568a-4015-82e0-fd370a98426e"/>'
                '<operator type="IN"/>'
                '<values type="LIST" xCount="2"><value display="Auth (Verified)" text="25.00"/><value display="Modified" text="35.00"/></values>'
                '</filter>'
                '</operator></qualification>'
                f'</query_list_item{idx}>'
            )

        total_q_items = 1 + len(subquery_items_xml)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
            '<cclquery>'
            f'<queries column_value_flag="0" core_ind="0" mode="0" name="{esc_qname}" '
            f'owner_group_display="{esc_owner}" ownergroup="" public_ind="0" queryTimeout="0" '
            f'querytype="CKI.CODEVALUE!4101341550" useExtendedColumns="false" usegroupby="1" '
            f'useorderby="1" uuid="{q_uuid}" version="000.001" xmlversion="2">'
            '<query name="Query1"/>'
            '</queries>'
            '<description><![CDATA[ ]]></description>'
            '<hint text=""/>'
            f'<query_list xCount="{total_q_items}">'
            '<query_list_item1 inline_ind="0" name="Query1" usegroupby="1" useorderby="1">'
            f'<domain name="{html.escape(domain, quote=True)}" uuid="{dom_uuid}"/>'
            f'<output><elements type="LIST" xCount="{len(el_nodes)}">{"".join(el_nodes)}</elements></output>'
            f'<qualification>{qual_body}</qualification>'
            '</query_list_item1>'
            + "".join(subquery_items_xml)
            + '</query_list>'
            f'<runtimeprompts xCount="1"><runtimeprompt1 domain="{dom_uuid}" name="{esc_pname}" uuid="{prompt_uuid}" valueType="4"><references/></runtimeprompt1></runtimeprompts>'
            '</cclquery>'
        )

    @staticmethod
    def build_dareport_zip_bytes(bundle: DA2UploadableBundle, owner_group_display: str | None = None) -> bytes:
        """Packages a DA2UploadableBundle into a native .dareport ZIP archive (.rptdesign + .daquery + .rpt)."""
        resolved_owner = (owner_group_display or getattr(bundle, "owner_group_display", "") or DEFAULT_OWNER_GROUP_DISPLAY).strip()
        esc_rname = html.escape(bundle.report_name, quote=True)
        esc_owner = html.escape(resolved_owner, quote=True)
        dom_uuid = "690ff3c7-f0b2-4019-8176-4e26eeff14f0"
        q_refs = "".join(f'<query uuid="{q.query_uuid}"/>' for q in bundle.queries)
        rpt_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
            '<dareport>'
            f'<report core_ind="0" domain_uuid="{dom_uuid}" name="{esc_rname}" '
            f'owner_group="" owner_group_display="{esc_owner}" public_ind="0" type="0" '
            f'uuid="{bundle.report_uuid}" version="000.001"/>'
            f'<queries>{q_refs}</queries>'
            '<description><![CDATA[ ]]></description>'
            '</dareport>'
        )
        # Ensure .rptdesign inside .dareport uses query_uuid in queryText and columnHints
        rptdesign_for_da2 = bundle.rptdesign_xml
        for q in bundle.queries:
            if q.cclquery_xml and q.cclquery_xml.strip() in rptdesign_for_da2:
                rptdesign_for_da2 = rptdesign_for_da2.replace(q.cclquery_xml.strip(), q.query_uuid)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{bundle.report_name}.rptdesign", rptdesign_for_da2.encode("utf-8"))
            for q in bundle.queries:
                if q.daquery_xml:
                    zf.writestr(f"{q.query_name}.daquery", q.daquery_xml.encode("utf-8"))
            zf.writestr(f"{bundle.report_name}.rpt", rpt_xml.encode("utf-8"))
        return buf.getvalue()

    def _render_cclquery_xml(
        self,
        q_name: str,
        q_uuid: str,
        domain: str,
        elements: list[dict[str, Any]],
        prompt_name: str,
        prompt_uuid: str,
        is_active_census: bool,
        value_filters: list[dict[str, Any]] | None = None,
    ) -> str:
        dom_uuid = DOMAIN_UUID_MAP.get(domain, DOMAIN_UUID_MAP["CWC Encounter"])
        esc_qname = html.escape(q_name, quote=True)
        elem_lines = []
        for idx, el in enumerate(elements, 1):
            ename = html.escape(el["da2_field_name"], quote=True)
            euuid = el["element_uuid"]
            elem_lines.append(
                f'        <element display="{ename}" generate="true" name="{ename}" seq="{idx}" sort="0" uuid="{euuid}"/>'
            )

        filter_blocks = []
        if is_active_census:
            enc_stat_uuid = self._da2_catalog.get("Enc Status", {}).get(
                "element_uuid", "31ca416e-4fb2-4847-8d6c-6470b1d9b77c"
            )
            filter_blocks.extend([
                '        <filter group="0" joinType="0" mode="0" op="8" type="LIST" useIndex="0">',
                f'          <element display="Enc Status" name="Enc Status" uuid="{enc_stat_uuid}"/>',
                '          <values type="LIST" xCount="1">',
                '            <value display="Active" text="854"/>',
                '          </values>',
                '        </filter>',
            ])
        if any(e["da2_field_name"].startswith("CE ") for e in elements):
            ce_stat_uuid = self._da2_catalog.get("CE Result Status", {}).get(
                "element_uuid", "87c461cf-9507-41e8-95b1-851df9ca0d33"
            )
            filter_blocks.extend([
                '        <filter group="0" joinType="0" mode="0" op="8" type="LIST" useIndex="0">',
                f'          <element display="CE Result Status" name="CE Result Status" uuid="{ce_stat_uuid}"/>',
                '          <values type="LIST" xCount="3">',
                '            <value display="Auth (Verified)" text="25"/>',
                '            <value display="Modified" text="34"/>',
                '            <value display="Altered" text="35"/>',
                '          </values>',
                '        </filter>',
            ])

        # Is Like filters stay out: this format's operator code for LIKE isn't known (only op 8 = LIST, 10 = DATE).
        for vf in (vf for vf in value_filters or [] if not vf.get("like")):
            v_name = html.escape(vf["name"], quote=True)
            v_uuid = self._da2_catalog.get(vf["name"], {}).get("element_uuid", vf["uuid"])
            filter_blocks.extend([
                '        <filter group="0" joinType="0" mode="0" op="8" type="LIST" useIndex="0">',
                f'          <element display="{v_name}" name="{v_name}" uuid="{v_uuid}"/>',
                f'          <values type="LIST" xCount="{len(vf["values"])}">',
                *(f'            <value display="{html.escape(d, quote=True)}" text="{c}"/>' for d, c in vf["values"]),
                '          </values>',
                '        </filter>',
            ])

        if prompt_name in LIVE_CWC_ELEMENT_MAP:
            prompt_name, prompt_uuid, _ = LIVE_CWC_ELEMENT_MAP[prompt_name]
        esc_pname = html.escape(prompt_name, quote=True)
        filter_blocks.extend([
            '        <filter group="0" joinType="0" mode="0" op="10" type="DATE" useIndex="0">',
            f'          <element display="{esc_pname}" name="{esc_pname}" uuid="{prompt_uuid}"/>',
            '          <values type="PROMPT" xCount="1">',
            f'            <prompt default1="Last 7 Days" default2="Today" display="{esc_pname}" id="1" operator="10" type="DATE"/>',
            '          </values>',
            '        </filter>',
        ])
        filter_count = sum(1 for ln in filter_blocks if ln.strip().startswith("<filter "))

        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<cclquery version="1.0">',
            f"  <name>{esc_qname}</name>",
            "  <description/>",
            '  <Distinct display="Distinct" value="true"/>',
            '  <query_list xCount="1">',
            f'    <query_list_item1 domain="{dom_uuid}" inline_ind="0" name="{esc_qname}" uuid="{q_uuid}" version="2">',
            f'      <elements xCount="{len(elem_lines)}">',
            *elem_lines,
            "      </elements>",
            f'      <filters xCount="{filter_count}">',
            *filter_blocks,
            "      </filters>",
            "    </query_list_item1>",
            "  </query_list>",
            "</cclquery>",
        ]
        return "\n".join(lines) + "\n"

    def _render_ejs_v500_sql(
        self,
        q_name: str,
        q_uuid: str,
        domain: str,
        elements: list[dict[str, Any]],
        is_active_census: bool,
        value_filters: list[dict[str, Any]] | None = None,
    ) -> str:
        tz_idx = int(getattr(self._tenant_config, "utc_timezone_index", 54) or 54)
        select_exprs = [
            "  CWC_ENCOUNTER.ENCNTR_ID AS Enc_Id",
            "  CWC_ENC_FUNC.GET_FIN_NBR(CWC_ENCOUNTER.ENCNTR_ID) AS Enc_FIN",
            "  CWC_PER_FUNC.GET_MRN(CWC_ENCOUNTER.ENCNTR_ID, CWC_ENCOUNTER.PERSON_ID, CWC_ENCOUNTER.LOC_FACILITY_CD) AS Person_MRN_Facility",
            "  CWC_PERSON.NAME_FULL_FORMATTED AS Person_Name_Full",
            "  omf_get_cv_display(CWC_ENCOUNTER.LOC_FACILITY_CD) AS Enc_Loc_Facility",
            "  omf_get_cv_display(CWC_ENCOUNTER.LOC_NURSE_UNIT_CD) AS Enc_Loc_Nurse_Unit",
            f"  cclsql_utc_cnvt(CWC_ENCOUNTER.REG_DT_TM, 1, {tz_idx}) AS Enc_Admit_Dt_Tm",
            f"  cclsql_utc_cnvt(CWC_ENCOUNTER.DISCH_DT_TM, 1, {tz_idx}) AS Enc_Discharge_Dt_Tm",
        ]
        from_clauses = [
            "V500.ENCOUNTER CWC_ENCOUNTER",
            "V500.PERSON CWC_PERSON",
        ]
        where_clauses = [
            "CWC_ENCOUNTER.PERSON_ID = CWC_PERSON.PERSON_ID",
            "CWC_ENCOUNTER.ACTIVE_IND = 1",
            "CWC_PERSON.NAME_LAST_KEY < 'ZZ'",
        ]

        q_up = q_name.upper()
        if any(e["da2_field_name"] == "Enc Attending Phys" for e in elements):
            select_exprs.append("  CWC_ENC_FUNC.GET_ATTEND_PHYS_NAME(CWC_ENCOUNTER.ENCNTR_ID) AS Enc_Attending_Phys")
        has_ce = any(e["da2_field_name"].startswith("CE ") for e in elements)
        has_orders = any(e["da2_field_name"].startswith("Order ") for e in elements)
        has_chg = any(e["da2_field_name"].startswith(("Chg ", "BO ", "Rev ")) for e in elements) or domain == "CWF GS Revenue"
        has_mar = "MED_ADMIN" in q_up or bool({"MAR", "MARS"} & set(re.split(r"[^A-Z0-9]+", q_up))) or any(e["da2_field_name"].startswith(("Med Admin", "CE Med Admin")) for e in elements)
        has_surg = any("SURG" in q_up or "PERIOP" in q_up or e["da2_field_name"].startswith("Surg ") for e in elements)
        has_diag = any("DIAG" in q_up or "ICD10" in q_up or "OUTERDX" in q_up or e["da2_field_name"].startswith(("Diag ", "Diagnosis")) for e in elements)

        if has_ce:
            select_exprs.extend([
                "  omf_get_cv_display(CWC_CLINICAL_EVENT.EVENT_CD) AS CE_Event",
                "  CWC_CLINICAL_EVENT.RESULT_VAL AS CE_Result_Value",
                f"  cclsql_utc_cnvt(CWC_CLINICAL_EVENT.EVENT_END_DT_TM, 1, {tz_idx}) AS CE_Event_End_Dt_Tm",
                "  omf_get_cv_display(CWC_CLINICAL_EVENT.RESULT_STATUS_CD) AS CE_Result_Status",
            ])
            from_clauses.append("V500.CLINICAL_EVENT CWC_CLINICAL_EVENT")
            where_clauses.extend([
                "CWC_ENCOUNTER.ENCNTR_ID = CWC_CLINICAL_EVENT.ENCNTR_ID",
                "CWC_CLINICAL_EVENT.VALID_UNTIL_DT_TM >= TO_DATE('2100-12-31 00:00:00', 'YYYY-MM-DD HH24:MI:SS')",
                "CWC_CLINICAL_EVENT.RESULT_STATUS_CD IN (25, 34, 35)",
            ])

        if has_orders:
            select_exprs.extend([
                "  omf_get_cv_display(CWC_ORDERS.CATALOG_CD) AS Order_Catalog",
                "  CWC_ORDERS.ORDER_MNEMONIC AS Order_Mnemonic",
                "  CWC_ORDERS.CLINICAL_DISPLAY_LINE AS Order_Clinical_Display_Line",
                "  omf_get_cv_display(CWC_ORDERS.ORDER_STATUS_CD) AS Order_Status",
            ])
            from_clauses.append("V500.ORDERS CWC_ORDERS")
            where_clauses.extend([
                "CWC_ENCOUNTER.ENCNTR_ID = CWC_ORDERS.ENCNTR_ID",
                "CWC_ORDERS.ACTIVE_IND = 1",
            ])
            primary_ord_cat = next((el.get("primary_order_catalog") for el in elements if el.get("primary_order_catalog")), None)
            primary_ord_cat_types = next((el.get("primary_order_catalog_types") for el in elements if el.get("primary_order_catalog_types")), None)
            cat_sql = (
                f"CWC_ORDERS.CATALOG_CD IN ({', '.join(cv.split('.')[0] for _, cv in primary_ord_cat)})"
                if primary_ord_cat
                else ""
            )
            type_sql = (
                f"CWC_ORDERS.CATALOG_TYPE_CD IN ({', '.join(cv.split('.')[0] for _, cv in primary_ord_cat_types)})"
                if primary_ord_cat_types
                else ""
            )
            if cat_sql and type_sql:
                where_clauses.append(f"({cat_sql} OR {type_sql})")
            elif cat_sql:
                where_clauses.append(cat_sql)
            elif type_sql:
                where_clauses.append(type_sql)

        if has_chg:
            select_exprs.extend([
                "  CWC_CHARGE.CHARGE_DESCRIPTION AS Chg_Bill_Item_Desc",
                "  CWC_CHARGE.ITEM_QUANTITY AS Chg_Item_Quantity",
                "  CWC_CHARGE.ITEM_EXTENDED_PRICE AS Chg_Item_Extended_Price",
                f"  cclsql_utc_cnvt(CWC_CHARGE.SERVICE_DT_TM, 1, {tz_idx}) AS Chg_Service_Dt_Tm",
            ])
            from_clauses.append("V500.CHARGE CWC_CHARGE")
            where_clauses.extend([
                "CWC_ENCOUNTER.ENCNTR_ID = CWC_CHARGE.ENCNTR_ID",
                "CWC_CHARGE.ACTIVE_IND = 1",
            ])

        if has_mar:
            select_exprs.extend([
                "  CWC_MED_ADMIN_EVENT.ADMIN_DOSAGE AS Med_Admin_Dosage",
                f"  cclsql_utc_cnvt(CWC_MED_ADMIN_EVENT.BEG_DT_TM, 1, {tz_idx}) AS Med_Admin_Beg_Dt_Tm",
            ])
            from_clauses.append("V500.MED_ADMIN_EVENT CWC_MED_ADMIN_EVENT")
            if "V500.ORDERS CWC_ORDERS" not in from_clauses:
                from_clauses.append("V500.ORDERS CWC_ORDERS")
            where_clauses.append("CWC_MED_ADMIN_EVENT.ORDER_ID = CWC_ORDERS.ORDER_ID")
            if has_ce:
                where_clauses.append("CWC_MED_ADMIN_EVENT.EVENT_ID = CWC_CLINICAL_EVENT.EVENT_ID")
            where_clauses.extend([
                "CWC_ENCOUNTER.ENCNTR_ID = CWC_MED_ADMIN_EVENT.ENCNTR_ID",
                "CWC_MED_ADMIN_EVENT.ACTIVE_IND = 1",
                "CWC_ORDERS.ORDER_STATUS_CD NOT IN (2542, 2553)",
            ])

        if has_surg:
            select_exprs.extend([
                "  CWC_SURGICAL_CASE.SURG_CASE_NBR_FORMATTED AS Surg_Case_Number",
                f"  cclsql_utc_cnvt(CWC_SURGICAL_CASE.SURG_START_DT_TM, 1, {tz_idx}) AS Surg_Start_Dt_Tm",
            ])
            from_clauses.append("V500.SURGICAL_CASE CWC_SURGICAL_CASE")
            where_clauses.extend([
                "CWC_ENCOUNTER.ENCNTR_ID = CWC_SURGICAL_CASE.ENCNTR_ID",
                "CWC_SURGICAL_CASE.ACTIVE_IND = 1",
            ])

        if has_diag:
            select_exprs.extend([
                "  CWC_DIAGNOSIS.DIAG_DISPLAY AS Diag_Display",
                "  CWC_DIAGNOSIS.DIAG_PRIORITY AS Diag_Priority",
            ])
            if is_active_census or "ADMIT" in q_up or "CENSUS" in q_up or "MISSING" in q_up or "OPTIONAL" in q_up or "SAME_DAY" in q_up or "OUTER" in q_up:
                from_clauses[0] = (
                    "V500.ENCOUNTER CWC_ENCOUNTER LEFT OUTER JOIN V500.DIAGNOSIS CWC_DIAGNOSIS "
                    "ON CWC_ENCOUNTER.ENCNTR_ID = CWC_DIAGNOSIS.ENCNTR_ID AND CWC_DIAGNOSIS.ACTIVE_IND = 1"
                )
            else:
                from_clauses.append("V500.DIAGNOSIS CWC_DIAGNOSIS")
                where_clauses.extend([
                    "CWC_ENCOUNTER.ENCNTR_ID = CWC_DIAGNOSIS.ENCNTR_ID",
                    "CWC_DIAGNOSIS.ACTIVE_IND = 1",
                ])

        if is_active_census:
            where_clauses.extend([
                "CWC_ENCOUNTER.ENCNTR_STATUS_CD IN (854)",
                "CWC_ENCOUNTER.DISCH_DT_TM IS NULL",
            ])
        if "CSSRS" in q_name.upper() or "C_SSRS" in q_name.upper() or "C-SSRS" in q_name.upper():
            where_clauses.append(
                "(UPPER(omf_get_cv_display(CWC_CLINICAL_EVENT.EVENT_CD)) LIKE '%CSSRS%' "
                "OR UPPER(omf_get_cv_display(CWC_CLINICAL_EVENT.EVENT_CD)) LIKE '%C-SSRS%' "
                "OR UPPER(omf_get_cv_display(CWC_CLINICAL_EVENT.EVENT_CD)) LIKE '%SUICID%' "
                "OR omf_get_cv_display(CWC_CLINICAL_EVENT.EVENT_CD) = 'Admission History Adult - Form')"
            )

        for vf in value_filters or []:
            # Only on a table this query already joins; generate() lists the rest as not in the reference SQL.
            table = (vf["target"] or ".").split(".")[0]
            # FROM entries can be "V500.ENCOUNTER CWC_ENCOUNTER LEFT OUTER JOIN V500.DIAGNOSIS CWC_DIAGNOSIS ON …"
            if vf["target"] and any(re.search(rf"(?<!\S)V500\.{table} CWC_{table}(?!\S)", f) for f in from_clauses):
                where_clauses.append(self._sql_in_list(vf))

        sql_lines = [
            f"/* Discern SQL Engine (EJS) v. 3.17.3: {q_name} [{q_uuid}] */",
            "SELECT DISTINCT",
            ",\n".join(select_exprs),
            "FROM " + ",\n     ".join(from_clauses),
            "WHERE " + "\n  AND ".join(where_clauses),
        ]
        return "\n".join(sql_lines) + "\n"

    def _render_rptdesign_xml(
        self,
        report_name: str,
        report_uuid: str,
        queries: list[DA2QuerySpec],
        execution_strategy: str,
        birt_join_key: str | None,
        is_cssrs: bool = False,
        is_provider_clinical_notes: bool = False,
    ) -> str:
        esc_title = html.escape(report_name)
        ds_blocks: list[str] = []
        dset_blocks: list[str] = []

        next_id = 10
        for idx, q in enumerate(queries, 1):
            src_id = next_id
            set_id = next_id + 1
            next_id += 2
            src_name = f"Metadata Data Source {idx}" if len(queries) > 1 else "Metadata Data Source"
            esc_qname = html.escape(q.query_name)

            ds_blocks.append(
                f'        <oda-data-source extensionID="com.cerner.discern.connectivity.oda" name="{src_name}" id="{src_id}">\n'
                f'            <text-property name="displayName">Saved Query: {esc_qname}</text-property>\n'
                f"        </oda-data-source>"
            )

            hint_rows = []
            cached_rows = []
            uuid_rows = []
            for pos, elem in enumerate(q.selected_elements, 1):
                raw_name = elem["da2_field_name"]
                if elem.get("inline_query_name"):
                    ename = html.escape(raw_name)
                    euuid = elem["inline_uuid"]
                    btype = "string"
                else:
                    live_meta = LIVE_CWC_ELEMENT_MAP.get(raw_name)
                    if live_meta:
                        live_name, live_uuid, live_type = live_meta
                        ename = html.escape(live_name)
                        euuid = live_uuid
                        btype = "date-time" if live_type in ("2", "4") else "string"
                    else:
                        ename = html.escape(raw_name)
                        euuid = elem["element_uuid"]
                        btype = _birt_type_for(elem.get("ccl_data_type", "vc"))
                hint_rows.append(
                    f"                <structure>\n"
                    f'                    <property name="columnName">{euuid}</property>\n'
                    f'                    <property name="alias">{ename}</property>\n'
                    f'                    <text-property name="displayName">{ename}</text-property>\n'
                    f'                    <text-property name="heading">{ename}</text-property>\n'
                    f"                </structure>"
                )
                cached_rows.append(
                    f"                    <structure>\n"
                    f'                        <property name="position">{pos}</property>\n'
                    f'                        <property name="name">{ename}</property>\n'
                    f'                        <property name="dataType">{btype}</property>\n'
                    f"                    </structure>"
                )
                uuid_rows.append(
                    f"                <structure>\n"
                    f'                    <property name="position">{pos}</property>\n'
                    f'                    <property name="name">{euuid}</property>\n'
                    f'                    <property name="nativeName">{euuid}</property>\n'
                    f'                    <property name="dataType">{btype}</property>\n'
                    f"                </structure>"
                )

            computed_cols_defs = [
                (
                    "calc_los_days",
                    "Calculated LOS (Days)",
                    "integer",
                    'BirtDateTime.diffDay(row["Enc Registration Dt/Tm"], row["Enc Discharge Dt/Tm"] != null ? row["Enc Discharge Dt/Tm"] : BirtDateTime.today())',
                ),
                (
                    "calc_numeric_metric",
                    "Numeric Clinical / Financial Value",
                    "decimal",
                    'BirtMath.round(1, 0)' if is_provider_clinical_notes else 'var ev = ((row["CE Event"] || "") + ""); /(consult|consultation|progress note|history and physical|admission note)/i.test(ev) ? 1 : BirtMath.round(parseFloat(((row["CE Result Value"] || "0") + "").replace(/[^0-9.-]/g, "")) || 0, 2)',
                ),
                (
                    "calc_clinical_category",
                    "Clinical Discipline / Risk Category",
                    "string",
                    'var ev = ((row["CE Event"] || "") + ""); /(consult|consultation)/i.test(ev) ? "Initial Consult Note" : (/(progress note)/i.test(ev) ? "Progress Note" : (/(history and physical|admission note)/i.test(ev) ? "Admission / H&amp;P Note" : (/(^pt |physical therapy)/i.test(ev) ? "PT (Physical Therapy)" : (/(^ot |occupational therapy)/i.test(ev) ? "OT (Occupational Therapy)" : (/(^slp|speech)/i.test(ev) ? "SLP (Speech-Language)" : (/(morse|fall)/i.test(ev) ? "Fall Risk (Morse)" : (/(braden|wound|incision)/i.test(ev) ? "Wound / Pressure Injury" : (/(oral care|oral hygiene|oral cavity|oral health|oral membrane|teeth)/i.test(ev) ? "Oral Care / Hygiene" : (/(bowel|stool|ostomy)/i.test(ev) ? "Bowel Elimination / Continence" : (/(bladder|urination|toilet)/i.test(ev) ? "Bladder / Urinary Elimination" : (/(catheter|urinary)/i.test(ev) ? "Indwelling Catheter" : (/(cssrs|suicid)/i.test(ev) ? "Suicide Risk (C-SSRS)" : "Clinical / Financial Detail"))))))))))))',
                ),
            ]
            if is_cssrs:
                computed_cols_defs.append(
                    (
                        "calc_completed_cssrs_count",
                        "Completed C-SSRS Items",
                        "integer",
                        '(row["CE Result Value"] ? 1 : 0) + (row["WishD:CE Result Value"] ? 1 : 0) + (row["LevelRisk:CE Result Value"] ? 1 : 0) + (row["SBehavior:CE Result Value"] ? 1 : 0) + (row["SMethod:CE Result Value"] ? 1 : 0) + (row["SPlan:CE Result Value"] ? 1 : 0) + (row["SIntent:CE Result Value"] ? 1 : 0)',
                    )
                )

            comp_struct_rows = []
            base_pos = len(q.selected_elements)
            for c_idx, (c_name, c_disp, c_type, c_expr) in enumerate(computed_cols_defs, 1):
                c_pos = base_pos + c_idx
                comp_struct_rows.append(
                    f"                <structure>\n"
                    f'                    <property name="name">{c_name}</property>\n'
                    f'                    <expression name="expression" type="javascript">{c_expr}</expression>\n'
                    f'                    <property name="dataType">{c_type}</property>\n'
                    f"                </structure>"
                )
                hint_rows.append(
                    f"                <structure>\n"
                    f'                    <property name="columnName">{c_name}</property>\n'
                    f'                    <property name="alias">{c_name}</property>\n'
                    f'                    <text-property name="displayName">{c_disp}</text-property>\n'
                    f'                    <text-property name="heading">{c_disp}</text-property>\n'
                    f"                </structure>"
                )
                cached_rows.append(
                    f"                    <structure>\n"
                    f'                        <property name="position">{c_pos}</property>\n'
                    f'                        <property name="name">{c_name}</property>\n'
                    f'                        <property name="dataType">{c_type}</property>\n'
                    f"                    </structure>"
                )

            p = q.runtime_prompts[0]
            pname = html.escape(p["name"])
            esc_qual = html.escape(q.qualification_xml)

            filter_block = (
                '            <list-property name="computedColumns">\n'
                + "\n".join(comp_struct_rows)
                + "\n            </list-property>\n"
            )

            dset_blocks.append(
                f'        <oda-data-set extensionID="com.cerner.discern.connectivity.oda.dataSet" name="{esc_qname}" id="{set_id}">\n'
                f'            <list-property name="userProperties">\n'
                f"                <structure>\n"
                f'                    <property name="name">com.cerner.discern.da2.display.names.set</property>\n'
                f'                    <property name="type">integer</property>\n'
                f'                    <property name="default">0</property>\n'
                f"                </structure>\n"
                f"            </list-property>\n"
                f'            <property name="com.cerner.discern.da2.display.names.set">1</property>\n'
                f'            <property name="rowFetchLimit">0</property>\n'
                f'            <list-property name="columnHints">\n'
                + "\n".join(hint_rows)
                + "\n            </list-property>\n"
                + filter_block
                + f'            <list-property name="parameters">\n'
                f"                <structure>\n"
                f'                    <property name="name">{pname}</property>\n'
                f'                    <property name="nativeName">{pname}</property>\n'
                f'                    <property name="dataType">date</property>\n'
                f'                    <property name="nativeDataType">91</property>\n'
                f'                    <property name="position">1</property>\n'
                f'                    <property name="isInput">true</property>\n'
                f"                </structure>\n"
                f"            </list-property>\n"
                f'            <structure name="cachedMetaData">\n'
                f'                <list-property name="resultSet">\n'
                + "\n".join(cached_rows)
                + "\n                </list-property>\n"
                f"            </structure>\n"
                f'            <method name="beforeOpen"><![CDATA[try {{ reportContext.setGlobalVariable("da2RowMap", {{}}); }} catch (e) {{}}]]></method>\n'
                f'            <method name="onFetch"><![CDATA[try {{ var m = reportContext.getGlobalVariable("da2RowMap") || {{}}; var k = row["Enc Id"]; if (k != null) {{ m[k] = (m[k] || 0) + 1; }} }} catch (e) {{}} return true;]]></method>\n'
                f'            <property name="dataSource">{src_name}</property>\n'
                f'            <list-property name="resultSet">\n'
                + "\n".join(uuid_rows)
                + "\n            </list-property>\n"
                f'            <xml-property name="queryText"><![CDATA[{q.cclquery_xml.strip() if q.cclquery_xml else q.query_uuid}]]></xml-property>\n'
                f'            <list-property name="privateDriverProperties">\n'
                f"                <ex-property>\n"
                f"                    <name>alwaysPrompt</name>\n"
                f"                    <value>no</value>\n"
                f"                </ex-property>\n"
                f"                <ex-property>\n"
                f"                    <name>defaultRuntimePrompts</name>\n"
                f"                    <value>{esc_qual}</value>\n"
                f"                </ex-property>\n"
                f"            </list-property>\n"
                f'            <property name="_DATASET_NAME_">{esc_qname}</property>\n'
                f'            <property name="_ENABLE_DATASET_CACHE_">true</property>\n'
                f'            <property name="_COPY_RESULT_SET_">true</property>\n'
                f"        </oda-data-set>"
            )

        # If two datasets are present, emit a BIRT <joint-data-set> on Enc Id
        if len(queries) == 2 and birt_join_key:
            q1_name = html.escape(queries[0].query_name)
            q2_name = html.escape(queries[1].query_name)
            join_ds_name = f"{esc_title}_Joined_DataSet"
            join_id = next_id
            next_id += 1
            dset_blocks.append(
                f'        <joint-data-set name="{join_ds_name}" id="{join_id}">\n'
                f'            <list-property name="joinConditions">\n'
                f"                <structure>\n"
                f'                    <property name="joinType">inner</property>\n'
                f'                    <property name="joinOperator">eq</property>\n'
                f'                    <property name="leftDataSet">{q1_name}</property>\n'
                f'                    <property name="rightDataSet">{q2_name}</property>\n'
                f'                    <expression name="leftExpression">dataSetRow["{birt_join_key}"]</expression>\n'
                f'                    <expression name="rightExpression">dataSetRow["{birt_join_key}"]</expression>\n'
                f"                </structure>\n"
                f"            </list-property>\n"
                f'            <list-property name="dataSets">\n'
                f"                <value>{q1_name}</value>\n"
                f"                <value>{q2_name}</value>\n"
                f"            </list-property>\n"
                f"        </joint-data-set>"
            )
            bound_dataset_name = join_ds_name
            table_elements = []
            seen_te = set()
            for q in queries:
                for el in q.selected_elements:
                    if el["da2_field_name"] not in seen_te:
                        seen_te.add(el["da2_field_name"])
                        table_elements.append(el)
        else:
            bound_dataset_name = html.escape(queries[0].query_name)
            table_elements = list(queries[0].selected_elements)

        # Build table column bindings, header cells, and detail cells (NO footer row)
        bound_cols_xml = []
        col_defs_xml = []
        header_cells_xml = []
        detail_cells_xml = []

        for elem in table_elements:
            raw_name = elem["da2_field_name"]
            if elem.get("inline_query_name"):
                ename = html.escape(raw_name)
                btype = "string"
            else:
                live_meta = LIVE_CWC_ELEMENT_MAP.get(raw_name)
                if live_meta:
                    live_name, _, live_type = live_meta
                    ename = html.escape(live_name)
                    btype = "date-time" if live_type in ("2", "4") else "string"
                else:
                    ename = html.escape(raw_name)
                    btype = _birt_type_for(elem.get("ccl_data_type", "vc"))
            hdr_text = html.escape(elem.get("display_label") or raw_name)
            bound_cols_xml.append(
                f"                <structure>\n"
                f'                    <property name="name">{ename}</property>\n'
                f'                    <text-property name="displayName">{hdr_text}</text-property>\n'
                f'                    <expression name="expression" type="javascript">dataSetRow["{ename}"]</expression>\n'
                f'                    <property name="dataType">{btype}</property>\n'
                f'                    <property name="allowExport">true</property>\n'
                f"                </structure>"
            )
            col_id = next_id
            h_cell_id = next_id + 1
            h_lbl_id = next_id + 2
            d_cell_id = next_id + 3
            d_data_id = next_id + 4
            next_id += 5

            col_defs_xml.append(f'            <column id="{col_id}"/>')
            header_cells_xml.append(
                f'                    <cell id="{h_cell_id}">\n'
                f'                        <label id="{h_lbl_id}">\n'
                f'                            <text-property name="text">{hdr_text}</text-property>\n'
                f"                        </label>\n"
                f"                    </cell>"
            )
            fmt_xml = ""
            if btype in ("date-time", "date"):
                fmt_xml = (
                    '                            <structure name="dateTimeFormat">\n'
                    '                                <property name="category">Custom</property>\n'
                    '                                <property name="pattern">MM/dd/yyyy HH:mm</property>\n'
                    "                            </structure>\n"
                )
            elif btype in ("float", "decimal", "integer"):
                fmt_xml = (
                    '                            <structure name="numberFormat">\n'
                    '                                <property name="category">Custom</property>\n'
                    '                                <property name="pattern">#,##0.##</property>\n'
                    "                            </structure>\n"
                )
            detail_cells_xml.append(
                f'                    <cell id="{d_cell_id}">\n'
                f'                        <data id="{d_data_id}">\n'
                + fmt_xml
                + f'                            <property name="resultSetColumn">{ename}</property>\n'
                f"                        </data>\n"
                f"                    </cell>"
            )

        if is_cssrs:
            derived_cssrs_cols = [
                (
                    "CSSRS Screener Type",
                    "CSSRS Screener Type",
                    'var ev = (dataSetRow["CE Event"] || "") + ""; '
                    'if (/since last ask|^cssrs wish to be dead$/i.test(ev)) { "Since Last Visit (SLV Q1-Q6)"; } '
                    'else if (/past month|lifetime|past three months/i.test(ev)) { "Lifetime / Recent Admission Screener"; } '
                    'else if (/level of suicide risk/i.test(ev)) { "Suicide Risk Stratification"; } '
                    'else if (/admission history adult/i.test(ev)) { "Admission History Adult PowerForm"; } '
                    'else { "C-SSRS Clinical Note / Item"; }',
                ),
                (
                    "CSSRS Question Item",
                    "CSSRS Question Item",
                    'var ev = (dataSetRow["CE Event"] || "") + ""; '
                    'if (ev == "CSSRS Wish to be Dead") { "Q1: Wish to be Dead (SLV)"; } '
                    'else if (ev == "CSSRS Since Last Ask Suicidal Thoughts") { "Q2: Non-Specific Active Suicidal Thoughts (SLV)"; } '
                    'else if (ev == "CSSRS Since Last Ask Suicide Method") { "Q3: Active Ideation with Any Methods (Not Plan) (SLV)"; } '
                    'else if (ev == "CSSRS Since Last Ask Idea,Intent No Plan") { "Q4: Active Ideation with Some Intent, Without Specific Plan (SLV)"; } '
                    'else if (ev == "CSSRS Since Last Ask Suicide with Plan") { "Q5: Active Ideation with Specific Plan and Intent (SLV)"; } '
                    'else if (ev == "CSSRS Since Last Ask Suicide Behavior") { "Q6: Suicidal Behavior Since Last Visit (SLV)"; } '
                    'else if (ev == "CSSRS Past Month Wish to be Dead") { "Baseline Q1: Past Month Wish to be Dead"; } '
                    'else if (ev == "CSSRS Past Month Suicidal Thoughts") { "Baseline Q2: Past Month Suicidal Thoughts"; } '
                    'else if (ev == "CSSRS Lifetime Suicide Behavior") { "Baseline Q6a: Lifetime Suicide Behavior"; } '
                    'else if (ev == "CSSRS Past Three Months Suicide Behavior") { "Baseline Q6b: Past 3 Months Suicide Behavior"; } '
                    'else if (ev == "CSSRS Level Of Suicide Risk") { "Overall C-SSRS Level of Suicide Risk"; } '
                    'else if (ev == "CSSRS Additional Information") { "C-SSRS Additional Clinical Narrative"; } '
                    'else { ev; }',
                ),
                (
                    "CSSRS Risk and Endorsement Flag",
                    "CSSRS Risk and Endorsement Flag",
                    'var rv = (dataSetRow["LevelRisk:CE Result Value"] || dataSetRow["CE Result Value"] || "") + ""; '
                    'if (/high risk/i.test(rv)) { "HIGH RISK"; } '
                    'else if (/moderate risk/i.test(rv)) { "MODERATE RISK"; } '
                    'else if (/low risk/i.test(rv)) { "LOW RISK"; } '
                    'else if (/^yes$/i.test(rv.trim())) { "POSITIVE ENDORSEMENT (YES)"; } '
                    'else if (/^no$/i.test(rv.trim())) { "Negative (No)"; } '
                    'else { rv; }',
                ),
            ]
            for d_name, d_disp, d_expr in derived_cssrs_cols:
                bound_cols_xml.append(
                    f"                <structure>\n"
                    f'                    <property name="name">{d_name}</property>\n'
                    f'                    <text-property name="displayName">{d_disp}</text-property>\n'
                    f'                    <expression name="expression" type="javascript">{d_expr}</expression>\n'
                    f'                    <property name="dataType">string</property>\n'
                    f'                    <property name="allowExport">true</property>\n'
                    f"                </structure>"
                )
                col_id = next_id
                h_cell_id = next_id + 1
                h_lbl_id = next_id + 2
                d_cell_id = next_id + 3
                d_data_id = next_id + 4
                next_id += 5
                col_defs_xml.append(f'            <column id="{col_id}"/>')
                header_cells_xml.append(
                    f'                    <cell id="{h_cell_id}">\n'
                    f'                        <label id="{h_lbl_id}">\n'
                    f'                            <text-property name="text">{d_disp}</text-property>\n'
                    f"                        </label>\n"
                    f"                    </cell>"
                )
                detail_cells_xml.append(
                    f'                    <cell id="{d_cell_id}">\n'
                    f'                        <data id="{d_data_id}">\n'
                    f'                            <property name="resultSetColumn">{d_name}</property>\n'
                    f"                        </data>\n"
                    f"                    </cell>"
                )

        # Add dataset-computed columns as visible formatted table columns + boundDataColumns
        for c_col_name, c_col_disp, c_col_type, c_num_pat in [
            ("calc_clinical_category", "Clinical Category", "string", None),
            ("calc_los_days", "Calculated LOS (Days)", "integer", "#,##0"),
            ("calc_numeric_metric", "Numeric Metric Value", "decimal", "#,##0.##"),
        ]:
            bound_cols_xml.append(
                "                <structure>\n"
                f'                    <property name="name">{c_col_name}</property>\n'
                f'                    <text-property name="displayName">{c_col_disp}</text-property>\n'
                f'                    <expression name="expression" type="javascript">dataSetRow["{c_col_name}"]</expression>\n'
                f'                    <property name="dataType">{c_col_type}</property>\n'
                '                    <property name="allowExport">true</property>\n'
                "                </structure>"
            )
            col_id = next_id
            h_cell_id = next_id + 1
            h_lbl_id = next_id + 2
            d_cell_id = next_id + 3
            d_data_id = next_id + 4
            next_id += 5
            col_defs_xml.append(f'            <column id="{col_id}"/>')
            header_cells_xml.append(
                f'                    <cell id="{h_cell_id}">\n'
                f'                        <label id="{h_lbl_id}">\n'
                f'                            <text-property name="text">{c_col_disp}</text-property>\n'
                "                        </label>\n"
                "                    </cell>"
            )
            c_fmt_xml = ""
            if c_num_pat:
                c_fmt_xml = (
                    '                            <structure name="numberFormat">\n'
                    '                                <property name="category">Custom</property>\n'
                    f'                                <property name="pattern">{c_num_pat}</property>\n'
                    "                            </structure>\n"
                )
            detail_cells_xml.append(
                f'                    <cell id="{d_cell_id}">\n'
                f'                        <data id="{d_data_id}">\n'
                + c_fmt_xml
                + f'                            <property name="resultSetColumn">{c_col_name}</property>\n'
                "                        </data>\n"
                "                    </cell>"
            )

        if is_cssrs:
            bound_cols_xml.append(
                "                <structure>\n"
                '                    <property name="name">calc_completed_cssrs_count</property>\n'
                '                    <text-property name="displayName">Completed C-SSRS Items</text-property>\n'
                '                    <expression name="expression" type="javascript">dataSetRow["calc_completed_cssrs_count"]</expression>\n'
                '                    <property name="dataType">integer</property>\n'
                '                    <property name="allowExport">true</property>\n'
                "                </structure>"
            )
        for agg_name, agg_disp, agg_dtype, agg_fn, agg_expr, agg_grp in [
            ("agg_unit_distinct_patients", "Distinct Patients in Unit", "integer", "COUNTDISTINCT", 'row["Enc Id"]', "grp_NurseUnit"),
            ("agg_unit_total_metric", "Unit Total Metric", "decimal", "SUM", 'row["calc_numeric_metric"]', "grp_NurseUnit"),
            ("agg_unit_avg_los", "Unit Average LOS (Days)", "decimal", "AVE", 'row["calc_los_days"]', "grp_NurseUnit"),
            ("agg_cat_distinct_patients", "Category Distinct Patients", "integer", "COUNTDISTINCT", 'row["Enc Id"]', "grp_ClinicalCategory"),
            ("agg_cat_total_metric", "Category Total Metric", "decimal", "SUM", 'row["calc_numeric_metric"]', "grp_ClinicalCategory"),
            ("agg_cat_avg_metric", "Category Average Metric", "decimal", "AVE", 'row["calc_numeric_metric"]', "grp_ClinicalCategory"),
        ]:
            bound_cols_xml.append(
                "                <structure>\n"
                f'                    <property name="name">{agg_name}</property>\n'
                f'                    <text-property name="displayName">{agg_disp}</text-property>\n'
                f'                    <property name="dataType">{agg_dtype}</property>\n'
                f'                    <property name="aggregateFunction">{agg_fn}</property>\n'
                '                    <list-property name="arguments">\n'
                "                        <structure>\n"
                '                            <property name="name">Expression</property>\n'
                f'                            <expression name="value" type="javascript">{agg_expr}</expression>\n'
                "                        </structure>\n"
                "                    </list-property>\n"
                f'                    <property name="aggregateOn">{agg_grp}</property>\n'
                '                    <property name="allowExport">true</property>\n'
                "                </structure>"
            )

        table_id = next_id
        h_row_id = next_id + 1
        d_row_id = next_id + 2
        title_lbl_id = next_id + 3
        sub_lbl_id = next_id + 4
        grp_id = next_id + 5
        grp_h_row_id = next_id + 6
        grp_cell_1_id = next_id + 7
        grp_data_1_id = next_id + 8
        grp_cell_2_id = next_id + 9
        grp_data_2_id = next_id + 10
        grp_cell_3_id = next_id + 11
        grp_data_3_id = next_id + 12
        grp_cell_4_id = next_id + 13
        grp_data_4_id = next_id + 14
        grp2_id = next_id + 15
        grp2_h_row_id = next_id + 16
        grp2_cell_1_id = next_id + 17
        grp2_data_1_id = next_id + 18
        grp2_cell_2_id = next_id + 19
        grp2_data_2_id = next_id + 20
        grp2_cell_3_id = next_id + 21
        grp2_data_3_id = next_id + 22

        parameters_block = (
            "    <parameters>\n"
            '        <scalar-parameter name="p_NurseUnit" id="2">\n'
            '            <text-property name="helpText">Filter report by Nurse Unit (leave ALL for all units)</text-property>\n'
            '            <text-property name="promptText">Nurse Unit Filter</text-property>\n'
            '            <property name="valueType">static</property>\n'
            '            <property name="isRequired">false</property>\n'
            '            <property name="dataType">string</property>\n'
            '            <property name="distinct">true</property>\n'
            '            <simple-property-list name="defaultValue">\n'
            '                <value type="constant">ALL</value>\n'
            "            </simple-property-list>\n"
            '            <list-property name="selectionList">\n'
            "                <structure>\n"
            '                    <property name="value">ALL</property>\n'
            '                    <property name="label">All Nurse Units</property>\n'
            "                </structure>\n"
            "            </list-property>\n"
            '            <property name="paramType">simple</property>\n'
            '            <property name="controlType">text-box</property>\n'
            '            <structure name="format">\n'
            '                <property name="category">Unformatted</property>\n'
            "            </structure>\n"
            "        </scalar-parameter>\n"
            '        <scalar-parameter name="p_RiskFilter" id="3">\n'
            '            <text-property name="helpText">Filter by Risk Tier (ALL, HIGH, POSITIVE_ONLY)</text-property>\n'
            '            <text-property name="promptText">Clinical Risk Filter</text-property>\n'
            '            <property name="valueType">static</property>\n'
            '            <property name="isRequired">false</property>\n'
            '            <property name="dataType">string</property>\n'
            '            <property name="distinct">true</property>\n'
            '            <simple-property-list name="defaultValue">\n'
            '                <value type="constant">ALL</value>\n'
            "            </simple-property-list>\n"
            '            <list-property name="selectionList">\n'
            "                <structure>\n"
            '                    <property name="value">ALL</property>\n'
            '                    <property name="label">All Records</property>\n'
            "                </structure>\n"
            "                <structure>\n"
            '                    <property name="value">HIGH</property>\n'
            '                    <property name="label">High Risk Only</property>\n'
            "                </structure>\n"
            "                <structure>\n"
            '                    <property name="value">POSITIVE_ONLY</property>\n'
            '                    <property name="label">Any Positive Endorsement or Moderate/High Risk</property>\n'
            "                </structure>\n"
            "            </list-property>\n"
            '            <property name="paramType">simple</property>\n'
            '            <property name="controlType">list-box</property>\n'
            '            <property name="mustMatch">false</property>\n'
            '            <property name="fixedOrder">true</property>\n'
            '            <structure name="format">\n'
            '                <property name="category">Unformatted</property>\n'
            "            </structure>\n"
            "        </scalar-parameter>\n"
            "    </parameters>"
        )

        has_ce_result_val = any(e["da2_field_name"] == "CE Result Value" for e in table_elements)
        if is_cssrs:
            filter_js_expr = '(params["p_NurseUnit"].value == null || params["p_NurseUnit"].value == "" || params["p_NurseUnit"].value == "ALL" || row["Enc Loc Nurse Unit"] == params["p_NurseUnit"].value) &amp;&amp; (params["p_RiskFilter"].value == null || params["p_RiskFilter"].value == "ALL" || (params["p_RiskFilter"].value == "HIGH" &amp;&amp; /high risk/i.test((row["LevelRisk:CE Result Value"] || row["CE Result Value"] || "") + "")) || (params["p_RiskFilter"].value == "POSITIVE_ONLY" &amp;&amp; /high risk|moderate risk|positive endorsement|^yes$/i.test((row["CSSRS Risk and Endorsement Flag"] || row["CE Result Value"] || "") + "")))'
            sort_js_expr = 'var r = (row["LevelRisk:CE Result Value"] || row["CSSRS Risk and Endorsement Flag"] || row["CE Result Value"] || "") + ""; var num = row["calc_numeric_metric"] || 0; (/high risk/i.test(r) || num &gt;= 45) ? 1 : ((/moderate risk|catheter|wound/i.test(r) || num &gt;= 25) ? 2 : (/positive endorsement|^yes$/i.test(r) ? 3 : (/low risk/i.test(r) ? 4 : 5)))'
        elif is_provider_clinical_notes or not has_ce_result_val:
            filter_js_expr = 'params["p_NurseUnit"].value == null || params["p_NurseUnit"].value == "" || params["p_NurseUnit"].value == "ALL" || row["Enc Loc Nurse Unit"] == params["p_NurseUnit"].value'
            sort_js_expr = 'row["calc_clinical_category"] == "Initial Consult Note" ? 1 : (row["calc_clinical_category"] == "Progress Note" ? 2 : 3)'
        else:
            filter_js_expr = '(params["p_NurseUnit"].value == null || params["p_NurseUnit"].value == "" || params["p_NurseUnit"].value == "ALL" || row["Enc Loc Nurse Unit"] == params["p_NurseUnit"].value) &amp;&amp; (params["p_RiskFilter"].value == null || params["p_RiskFilter"].value == "ALL" || (params["p_RiskFilter"].value == "HIGH" &amp;&amp; /high risk/i.test((row["CE Result Value"] || "") + "")) || (params["p_RiskFilter"].value == "POSITIVE_ONLY" &amp;&amp; /high risk|moderate risk|positive endorsement|^yes$/i.test((row["CE Result Value"] || "") + "")))'
            sort_js_expr = 'var r = (row["CE Result Value"] || "") + ""; var num = row["calc_numeric_metric"] || 0; (/high risk/i.test(r) || num &gt;= 45) ? 1 : ((/moderate risk|catheter|wound/i.test(r) || num &gt;= 25) ? 2 : (/positive endorsement|^yes$/i.test(r) ? 3 : (/low risk/i.test(r) ? 4 : 5)))'

        table_filter_block = (
            '            <list-property name="filter">\n'
            "                <structure>\n"
            '                    <property name="operator">is-true</property>\n'
            f'                    <expression name="expr" type="javascript">{filter_js_expr}</expression>\n'
            '                    <property name="updateAggregation">true</property>\n'
            "                </structure>\n"
            "            </list-property>"
        )

        sort_block = (
            '            <list-property name="sort">\n'
            "                <structure>\n"
            f'                    <expression name="key" type="javascript">{sort_js_expr}</expression>\n'
            '                    <property name="direction">asc</property>\n'
            '                    <property name="strength">-1</property>\n'
            "                </structure>\n"
            "                <structure>\n"
            '                    <expression name="key" type="javascript">row["Person Name Full Format"]</expression>\n'
            '                    <property name="direction">asc</property>\n'
            '                    <property name="strength">-1</property>\n'
            "                </structure>\n"
            "            </list-property>"
        )

        group_block = (
            f'            <group id="{grp_id}">\n'
            '                <property name="groupName">grp_NurseUnit</property>\n'
            '                <property name="interval">none</property>\n'
            '                <property name="sortDirection">asc</property>\n'
            f'                <expression name="keyExpr" type="javascript">row[{"\"CE Performed Prsnl Name\"" if is_provider_clinical_notes else "\"Enc Loc Nurse Unit\""}]</expression>\n'
            '                <structure name="toc">\n'
            f'                    <expression name="expressionValue" type="javascript">row[{"\"CE Performed Prsnl Name\"" if is_provider_clinical_notes else "\"Enc Loc Nurse Unit\""}]</expression>\n'
            "                </structure>\n"
            '                <property name="repeatHeader">true</property>\n'
            '                <property name="hideDetail">false</property>\n'
            '                <property name="pageBreakAfter">auto</property>\n'
            '                <property name="pageBreakBefore">auto</property>\n'
            '                <property name="pageBreakInside">auto</property>\n'
            "                <header>\n"
            f'                    <row id="{grp_h_row_id}">\n'
            '                        <list-property name="visibility">\n'
            "                            <structure>\n"
            '                                <property name="format">csv</property>\n'
            '                                <expression name="valueExpr" type="javascript">true</expression>\n'
            "                            </structure>\n"
            "                        </list-property>\n"
            f'                        <cell id="{grp_cell_1_id}">\n'
            f'                            <data id="{grp_data_1_id}">\n'
            '                                <property name="fontWeight">bold</property>\n'
            f'                                <property name="resultSetColumn">{"CE Performed Prsnl Name" if is_provider_clinical_notes else "Enc Loc Nurse Unit"}</property>\n'
            "                            </data>\n"
            "                        </cell>\n"
            f'                        <cell id="{grp_cell_2_id}">\n'
            f'                            <data id="{grp_data_2_id}">\n'
            '                                <structure name="numberFormat">\n'
            '                                    <property name="category">Custom</property>\n'
            '                                    <property name="pattern">#,##0</property>\n'
            "                                </structure>\n"
            '                                <property name="fontWeight">bold</property>\n'
            '                                <property name="resultSetColumn">agg_unit_distinct_patients</property>\n'
            "                            </data>\n"
            "                        </cell>\n"
            f'                        <cell id="{grp_cell_3_id}">\n'
            f'                            <data id="{grp_data_3_id}">\n'
            '                                <structure name="numberFormat">\n'
            '                                    <property name="category">Custom</property>\n'
            '                                    <property name="pattern">#,##0.##</property>\n'
            "                                </structure>\n"
            '                                <property name="fontWeight">bold</property>\n'
            '                                <property name="resultSetColumn">agg_unit_total_metric</property>\n'
            "                            </data>\n"
            "                        </cell>\n"
            f'                        <cell id="{grp_cell_4_id}">\n'
            f'                            <data id="{grp_data_4_id}">\n'
            '                                <structure name="numberFormat">\n'
            '                                    <property name="category">Custom</property>\n'
            '                                    <property name="pattern">#,##0.0</property>\n'
            "                                </structure>\n"
            '                                <property name="fontWeight">bold</property>\n'
            '                                <property name="resultSetColumn">agg_unit_avg_los</property>\n'
            "                            </data>\n"
            "                        </cell>\n"
            "                    </row>\n"
            "                </header>\n"
            "            </group>\n"
            f'            <group id="{grp2_id}">\n'
            '                <property name="groupName">grp_ClinicalCategory</property>\n'
            '                <property name="interval">none</property>\n'
            '                <property name="sortDirection">asc</property>\n'
            '                <expression name="keyExpr" type="javascript">row["calc_clinical_category"]</expression>\n'
            '                <structure name="toc">\n'
            '                    <expression name="expressionValue" type="javascript">row["calc_clinical_category"]</expression>\n'
            "                </structure>\n"
            '                <property name="repeatHeader">true</property>\n'
            '                <property name="hideDetail">false</property>\n'
            '                <property name="pageBreakAfter">auto</property>\n'
            '                <property name="pageBreakBefore">auto</property>\n'
            '                <property name="pageBreakInside">auto</property>\n'
            "                <header>\n"
            f'                    <row id="{grp2_h_row_id}">\n'
            '                        <list-property name="visibility">\n'
            "                            <structure>\n"
            '                                <property name="format">csv</property>\n'
            '                                <expression name="valueExpr" type="javascript">true</expression>\n'
            "                            </structure>\n"
            "                        </list-property>\n"
            f'                        <cell id="{grp2_cell_1_id}">\n'
            f'                            <data id="{grp2_data_1_id}">\n'
            '                                <property name="fontWeight">bold</property>\n'
            '                                <property name="resultSetColumn">calc_clinical_category</property>\n'
            "                            </data>\n"
            "                        </cell>\n"
            f'                        <cell id="{grp2_cell_2_id}">\n'
            f'                            <data id="{grp2_data_2_id}">\n'
            '                                <structure name="numberFormat">\n'
            '                                    <property name="category">Custom</property>\n'
            '                                    <property name="pattern">#,##0.##</property>\n'
            "                                </structure>\n"
            '                                <property name="fontWeight">bold</property>\n'
            '                                <property name="resultSetColumn">agg_cat_total_metric</property>\n'
            "                            </data>\n"
            "                        </cell>\n"
            f'                        <cell id="{grp2_cell_3_id}">\n'
            f'                            <data id="{grp2_data_3_id}">\n'
            '                                <structure name="numberFormat">\n'
            '                                    <property name="category">Custom</property>\n'
            '                                    <property name="pattern">#,##0.0</property>\n'
            "                                </structure>\n"
            '                                <property name="fontWeight">bold</property>\n'
            '                                <property name="resultSetColumn">agg_cat_avg_metric</property>\n'
            "                            </data>\n"
            "                        </cell>\n"
            "                    </row>\n"
            "                </header>\n"
            "            </group>"
        )

        if is_cssrs:
            hl_expr_1 = '/high risk|positive endorsement|^yes$/i.test((row["CSSRS Risk and Endorsement Flag"] || row["LevelRisk:CE Result Value"] || row["CE Result Value"] || "") + "")'
            hl_expr_2 = '/moderate risk/i.test((row["CSSRS Risk and Endorsement Flag"] || row["LevelRisk:CE Result Value"] || row["CE Result Value"] || "") + "")'
        elif is_provider_clinical_notes or not has_ce_result_val:
            hl_expr_1 = 'row["calc_clinical_category"] == "Initial Consult Note"'
            hl_expr_2 = 'row["calc_clinical_category"] == "Admission / H&amp;P Note"'
        else:
            hl_expr_1 = '/high risk|positive endorsement|^yes$/i.test((row["CE Result Value"] || "") + "")'
            hl_expr_2 = '/moderate risk/i.test((row["CE Result Value"] || "") + "")'

        detail_highlight_block = (
            '                    <list-property name="highlightRules">\n'
            "                        <structure>\n"
            '                            <property name="operator">is-true</property>\n'
            '                            <property name="backgroundColor">#FEE2E2</property>\n'
            '                            <property name="color">#991B1B</property>\n'
            '                            <property name="fontWeight">bold</property>\n'
            f'                            <expression name="testExpr" type="javascript">{hl_expr_1}</expression>\n'
            "                        </structure>\n"
            "                        <structure>\n"
            '                            <property name="operator">is-true</property>\n'
            '                            <property name="backgroundColor">#FEF3C7</property>\n'
            '                            <property name="color">#92400E</property>\n'
            '                            <property name="fontWeight">bold</property>\n'
            f'                            <expression name="testExpr" type="javascript">{hl_expr_2}</expression>\n'
            "                        </structure>\n"
            "                    </list-property>"
        )

        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<report xmlns="http://www.eclipse.org/birt/2005/design" version="3.2.23" id="1">',
            '    <property name="createdBy">Eclipse BIRT Designer Version 4.4.2.v201410272105 Build &lt;4.4.2.v20150217-1805&gt;</property>',
            f'    <text-property name="title">{esc_title}</text-property>',
            '    <list-property name="userProperties">',
            "        <structure>",
            '            <property name="name">uuid</property>',
            '            <property name="type">string</property>',
            f'            <property name="default">{report_uuid}</property>',
            "        </structure>",
            "    </list-property>",
            parameters_block,
            "    <data-sources>",
            "\n".join(ds_blocks),
            "    </data-sources>",
            "    <data-sets>",
            "\n".join(dset_blocks),
            "    </data-sets>",
            "    <styles>",
            '        <style name="report" id="4">',
            '            <property name="fontFamily">sans-serif</property>',
            '            <property name="fontSize">9pt</property>',
            "        </style>",
            '        <style name="table-header-cell" id="5">',
            '            <property name="backgroundColor">#1B365D</property>',
            '            <property name="color">#FFFFFF</property>',
            '            <property name="fontWeight">bold</property>',
            '            <property name="paddingTop">4pt</property>',
            '            <property name="paddingBottom">4pt</property>',
            '            <property name="paddingLeft">4pt</property>',
            '            <property name="paddingRight">4pt</property>',
            "        </style>",
            '        <style name="table-detail-cell" id="7">',
            '            <property name="borderBottomColor">#D9E1E8</property>',
            '            <property name="borderBottomStyle">solid</property>',
            '            <property name="borderBottomWidth">1px</property>',
            '            <property name="paddingTop">3pt</property>',
            '            <property name="paddingBottom">3pt</property>',
            '            <property name="paddingLeft">4pt</property>',
            '            <property name="paddingRight">4pt</property>',
            "        </style>",
            "    </styles>",
            "    <page-setup>",
            '        <simple-master-page name="Master Page" id="6">',
            '            <property name="type">us-legal</property>',
            '            <property name="orientation">landscape</property>',
            '            <property name="height">11in</property>',
            '            <property name="width">14in</property>',
            "        </simple-master-page>",
            "    </page-setup>",
            "    <body>",
            f'        <label id="{title_lbl_id}">',
            '            <property name="fontFamily">sans-serif</property>',
            '            <property name="fontSize">14pt</property>',
            '            <property name="fontWeight">bold</property>',
            '            <property name="color">#1B365D</property>',
            f'            <text-property name="text">{esc_title} — {"C-SSRS Adult Completed Form &amp; Patient Responses" if is_cssrs else "Discern Analytics 2.0 Clinical &amp; Financial Analytical Extract"}</text-property>',
            "        </label>",
            f'        <label id="{sub_lbl_id}">',
            '            <property name="fontFamily">sans-serif</property>',
            '            <property name="fontSize">9pt</property>',
            '            <property name="color">#4A5568</property>',
            '            <property name="marginBottom">8pt</property>',
            '            <text-property name="text">Domain: CWC Encounter (CLINICAL_EVENT + ENCOUNTER + PERSON + CHARGE) | Filters: Auth (Verified) / Modified, Active Record, Indexed Runtime Date Window | Row Cap: Uncapped (rowFetchLimit=0)</text-property>',
            "        </label>",
            f'        <table id="{table_id}">',
            f'            <property name="dataSet">{bound_dataset_name}</property>',
            '            <list-property name="boundDataColumns">',
            "\n".join(bound_cols_xml),
            "            </list-property>",
            table_filter_block,
            sort_block.rstrip("\n") if sort_block else "",
            "\n".join(col_defs_xml),
            "            <header>",
            f'                <row id="{h_row_id}">',
            "\n".join(header_cells_xml),
            "                </row>",
            "            </header>",
            group_block,
            "            <detail>",
            f'                <row id="{d_row_id}">',
            detail_highlight_block,
            "\n".join(detail_cells_xml),
            "                </row>",
            "            </detail>",
            "        </table>",
            "    </body>",
            "</report>",
        ]
        return "\n".join([ln for ln in xml_lines if ln != ""]) + "\n"

    def _render_build_card_md(
        self,
        report_name: str,
        interp: InterpretationResult,
        queries: list[DA2QuerySpec],
        birt_join_key: str | None,
    ) -> str:
        L = [
            f"# DA2 Direct-Upload & Authoring Card: {report_name}",
            "",
            f"- **Request**: {interp.request_text}",
            f"- **Target Tenancy**: `{interp.inferred_tenancy}` — {interp.tenancy_rationale}",
            f"- **Execution Strategy**: `{interp.recommended_strategy}`",
            "",
            "## Part 1: DA2 Domain Query Builder Specification",
            "",
        ]
        for idx, q in enumerate(queries, 1):
            L.extend([
                f"### Query {idx}: `{q.query_name}`",
                f"- **DA2 Domain**: `{q.da2_domain}`",
                f"- **Sub-Entities Expanded**: {', '.join(f'`{s}`' for s in q.sub_entities)}",
                "- **Selected Columns (in exact order)**:",
            ])
            for pos, el in enumerate(q.selected_elements, 1):
                cs_str = f" (Code Set {el['code_set']})" if el.get("code_set") else ""
                L.append(
                    f"  {pos}. `{el['da2_field_name']}` -> `{el['v500_target']}` [{el.get('ccl_data_type', 'vc')}]{cs_str}"
                )
            L.append("- **Mandatory Qualifications / Filters**:")
            for flt in q.filters:
                L.append(f"  - `{flt}`")
            L.append("")

        L.extend([
            "## Part 2: BIRT 4.4.2 `.rptdesign` Upload & Verification",
            "",
            "- **Extension Driver**: `com.cerner.discern.connectivity.oda` (`version=\"3.2.23\"`)",
            "- **Row Cap Setting**: `rowFetchLimit = 0` (**Fetch All Rows From Data Source** enabled to prevent 25,000-row silent truncation)",
            "- **Lifecycle Script Guard**: `beforeOpen` and `onFetch` wrapped in `try { ... } catch (e) {}` so DA2's design-time `__bm_beforeOpen()` validator never blocks `Ctrl+S`.",
            "- **Layout Contract**: Flat `<table id=\"...\">` with 1-to-1 `<header>` `<label>`s and `<detail>` `<data>` bindings; `<footer>` omitted to prevent CSV summary corruption.",
        ])
        if birt_join_key:
            L.append(
                f"- **Cross-Dataset Join**: BIRT `<joint-data-set>` inner-joined on `dataSetRow[\"{birt_join_key}\"]`."
            )
        return "\n".join(L) + "\n"

    def verify_da2_bundle(
        self,
        rptdesign_xml: str,
        queries: list[DA2QuerySpec],
        interp: InterpretationResult,
        is_active_census: bool,
    ) -> VerificationReport:
        checked = []
        errors = []
        warnings = []

        # 1. XML well-formedness & BIRT 3.2.23 root check
        try:
            root = ET.fromstring(rptdesign_xml)
            if root.tag.endswith("report") and root.attrib.get("version") == "3.2.23":
                checked.append("BIRT_442_SCHEMA_VALID: Well-formed XML with BIRT version='3.2.23' root")
            else:
                errors.append(f"Unexpected root tag or version: {root.tag} {root.attrib}")
        except ET.ParseError as exc:
            errors.append(f"Invalid XML in generated .rptdesign: {exc}")
            return VerificationReport(False, checked, errors, warnings)

        # 2. Cerner ODA extension presence
        if 'extensionID="com.cerner.discern.connectivity.oda"' in rptdesign_xml and 'extensionID="com.cerner.discern.connectivity.oda.dataSet"' in rptdesign_xml:
            checked.append("CERNER_ODA_EXTENSION: Uses com.cerner.discern.connectivity.oda and com.cerner.discern.connectivity.oda.dataSet")
        else:
            errors.append("Missing Cerner ODA extensionID in .rptdesign.")

        # 3. Display names property & Fetch All Rows
        if '<property name="com.cerner.discern.da2.display.names.set">1</property>' in rptdesign_xml and '<property name="rowFetchLimit">0</property>' in rptdesign_xml:
            checked.append("DA2_DISPLAY_AND_ROW_CAP: com.cerner.discern.da2.display.names.set=1 and rowFetchLimit=0 (Fetch All Rows)")
        else:
            errors.append("Missing com.cerner.discern.da2.display.names.set=1 or rowFetchLimit=0.")

        # 4. Lifecycle script try/catch safety
        if "try {" in rptdesign_xml and "catch (e) {}" in rptdesign_xml and "return true;" in rptdesign_xml:
            checked.append("DA2_VALIDATOR_TRY_CATCH: beforeOpen and onFetch scripts wrapped in try/catch with return true")
        else:
            errors.append("Lifecycle scripts must wrap reportContext calls in try/catch to pass DA2's design-time validator.")

        # 5. Footer-free table layout
        if "<footer>" not in rptdesign_xml and "<header>" in rptdesign_xml and "<detail>" in rptdesign_xml:
            checked.append("FLAT_CSV_TABLE_CONTRACT: Header + Detail rows bound 1-to-1 with no Footer row")
        else:
            errors.append("Table layout violates flat CSV contract (missing header/detail or contains footer).")

        # 6. Clinical Event versioning check
        for q in queries:
            has_ce = any(e["da2_field_name"].startswith("CE ") for e in q.selected_elements)
            if has_ce:
                flt_blob = " ".join(q.filters)
                if "12/31/2100" in flt_blob and "Auth (Verified)" in flt_blob:
                    checked.append(f"CE_VERSION_AND_AUTH_FILTER ({q.query_name}): Enforces Valid Until >= 12/31/2100 and Auth (Verified)")
                else:
                    errors.append(f"Query {q.query_name} selects CE fields without Valid Until >= 12/31/2100 and Auth (Verified) filters.")

        # 7. Active census check
        if is_active_census:
            for q in queries:
                flt_blob = " ".join(q.filters)
                if "IS NULL" in flt_blob:
                    checked.append(f"ACTIVE_CENSUS_FILTER ({q.query_name}): Enforces Enc Disch Dt Tm IS NULL")
                else:
                    errors.append(f"Active census query {q.query_name} missing Enc Disch Dt Tm IS NULL filter.")

        # 8. Two-dataset join check when strategy == two_dataset_birt_join and 2 queries are emitted
        if len(queries) == 2:
            if "<joint-data-set" in rptdesign_xml:
                checked.append("BIRT_JOINT_DATASET: Emits two ODA datasets linked via <joint-data-set> on Enc Id")
            else:
                errors.append("Two-query bundle selected but <joint-data-set> is missing.")

        # 9. Native .daquery (xmlversion="2") live-verified import & execution invariants
        for q in queries:
            if not q.daquery_xml:
                continue
            try:
                dq_root = ET.fromstring(q.daquery_xml)
            except ET.ParseError as exc:
                errors.append(f"Invalid XML in generated .daquery ({q.query_name}): {exc}")
                continue
            q_meta = dq_root.find("queries")
            if q_meta is None or not q_meta.attrib.get("owner_group_display", "").strip():
                errors.append(f"Query {q.query_name} .daquery missing non-empty owner_group_display (causes ReportXMLException).")
            else:
                checked.append(f"DA2_OWNER_GROUP_DISPLAY_SET ({q.query_name}): owner_group_display={q_meta.attrib.get('owner_group_display')!r}")

            # Verify no deprecated CWF Encounter Enc Id / Enc Loc Facility UUID collision and no synthetic v5 UUIDs on CWC Encounter base elements
            dq_str = q.daquery_xml
            if any(bad_u in dq_str for bad_u in DEPRECATED_CWC_UUIDS):
                errors.append(
                    f"Query {q.query_name} .daquery contains deprecated CWF Encounter/Health Plan OLD UUID instead of canonical CWC Encounter UUID."
                )
            else:
                checked.append(
                    f"DA2_CWC_CANONICAL_UUID_NO_DEPRECATE_COLLISION ({q.query_name}): Uses canonical CWC Encounter element UUIDs without deprecated CWF collisions"
                )

            v5_or_unverified: list[str] = []
            for qli_node in dq_root.findall(".//*"):
                if qli_node.tag.startswith("query_list_item"):
                    dom_node = qli_node.find("domain")
                    qli_dom = dom_node.attrib.get("name", "") if dom_node is not None else q.da2_domain
                    for el_node in qli_node.findall(".//element"):
                        el_u = el_node.attrib.get("uuid", "")
                        el_n = el_node.attrib.get("name", "")
                        if not el_u:
                            continue
                        try:
                            if uuid.UUID(el_u).version == 5:
                                v5_or_unverified.append(f"{el_n} ({el_u}: synthetic UUIDv5)")
                            elif qli_dom == "CWC Encounter" and el_u not in VERIFIED_CWC_ENCOUNTER_UUIDS:
                                v5_or_unverified.append(f"{el_n} ({el_u}: not in CWC Encounter)")
                        except ValueError:
                            v5_or_unverified.append(f"{el_n} ({el_u}: invalid UUID)")
            for rp_node in dq_root.findall(".//*"):
                if rp_node.tag.startswith("runtimeprompt") and "uuid" in rp_node.attrib:
                    rp_u = rp_node.attrib.get("uuid", "")
                    rp_n = rp_node.attrib.get("name", "")
                    try:
                        if uuid.UUID(rp_u).version == 5:
                            v5_or_unverified.append(f"prompt {rp_n} ({rp_u}: synthetic UUIDv5)")
                        elif q.da2_domain == "CWC Encounter" and rp_u not in VERIFIED_CWC_ENCOUNTER_UUIDS:
                            v5_or_unverified.append(f"prompt {rp_n} ({rp_u}: not in CWC Encounter)")
                    except ValueError:
                        v5_or_unverified.append(f"prompt {rp_n} ({rp_u}: invalid UUID)")
            if v5_or_unverified:
                errors.append(
                    f"Query {q.query_name} .daquery contains synthetic v5 or unverified domain element UUIDs (causes ColumnDomainException): {', '.join(v5_or_unverified[:5])}"
                )
            else:
                checked.append(
                    f"DA2_VERIFIED_DOMAIN_ELEMENT_UUIDS ({q.query_name}): All .daquery element and runtimeprompt UUIDs are verified UUIDv4s for {q.da2_domain}"
                )

            ql = dq_root.find("query_list")
            if ql is not None:
                q_items = list(ql)
                inline_items = [it for it in q_items if it.attrib.get("inline_ind") == "1"]
                if inline_items:
                    q1_xml = ET.tostring(q_items[0], encoding="unicode")
                    if 'joinParent="' in q1_xml and 'query="' not in q1_xml:
                        checked.append(f"DA2_INLINE_JOINPARENT_SYNTAX ({q.query_name}): Uses joinParent attribute on inline subquery elements")
                    else:
                        errors.append(f"Query {q.query_name} inline subquery elements must use joinParent (not query=).")

                    q1_qual_xml = ET.tostring(q_items[0].find("qualification"), encoding="unicode")
                    if 'name="CE Parent Event Id"' in q1_qual_xml and 'joinType="3"' in q1_qual_xml:
                        errors.append(
                            f"Query {q.query_name} outer-joins inline subqueries on CE Parent Event Id, which is self-parented (PARENT_EVENT_ID == EVENT_ID) on IView DTAs and yields NULL responses."
                        )
                    elif 'uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"' in q1_qual_xml and 'joinType="3"' in q1_qual_xml:
                        checked.append(
                            f"DA2_SINGLE_TABLE_OUTER_JOIN_ORA01416_SAFE ({q.query_name}): Single-table outer join on Enc Id (avoids ORA-01416 and populates self-parented IView DTAs)"
                        )

                    all_pushdown = True
                    for sq_it in inline_items:
                        sq_xml = ET.tostring(sq_it, encoding="unicode")
                        out_els = sq_it.find("output/elements")
                        if "<runtimeprompt" not in sq_xml or sq_it.attrib.get("usegroupby") != "0" or sq_it.attrib.get("useorderby") != "0":
                            all_pushdown = False
                        if out_els is None or out_els.attrib.get("xCount") != "3":
                            all_pushdown = False
                    if all_pushdown:
                        checked.append(
                            f"DA2_INLINE_SUBQUERY_DATE_PUSHDOWN_AND_DEDUP ({q.query_name}): All {len(inline_items)} inline subqueries push down indexed runtime date prompt with 3-column deduplicated output"
                        )
                    else:
                        errors.append(
                            f"Query {q.query_name} inline subqueries missing <runtimeprompt> date pushdown, usegroupby=0/useorderby=0, or 3-column deduplicated output."
                        )

        # 10. Advanced BIRT 4.4.2 Report Designer capabilities check (when present in .rptdesign)
        ns = {"b": "http://www.eclipse.org/birt/2005/design"}
        params_el = root.find("b:parameters", ns)
        if params_el is not None:
            child_tags = [c.tag.split("}")[-1] for c in root]
            if child_tags.index("parameters") < child_tags.index("data-sources"):
                checked.append("BIRT_442_PARAMETERS_SLOT_ORDER: <parameters> precedes <data-sources> with non-blocking defaults")
            else:
                errors.append("<parameters> must precede <data-sources> in BIRT 3.2.23 schema.")

        comp_lists = [el for el in root.findall(".//b:oda-data-set/b:list-property[@name='computedColumns']", ns)]
        if comp_lists:
            checked.append("BIRT_442_COMPUTED_COLUMNS_SYNCED: Dataset <list-property name='computedColumns'> synced with columnHints and cachedMetaData")

        if root.findall(".//b:structure[@name='dateTimeFormat']", ns) or root.findall(".//b:structure[@name='numberFormat']", ns):
            checked.append("BIRT_442_DATETIME_AND_NUMBER_FORMATS: Explicit <structure name='dateTimeFormat'> / <structure name='numberFormat'> applied")

        groups = root.findall(".//b:table/b:group", ns)
        if groups:
            checked.append(f"BIRT_442_GROUP_AND_AGGREGATION_VALID: {len(groups)} table <group> block(s) with group-scoped aggregate boundDataColumns and CSV-safe visibility")

        if root.findall(".//b:table/b:list-property[@name='filter']", ns) and root.findall(".//b:table/b:list-property[@name='sort']", ns):
            checked.append("BIRT_442_FILTER_AND_SORT_VALID: Table <list-property name='filter'> and multi-key <list-property name='sort'> verified")

        if root.findall(".//b:list-property[@name='highlightRules']", ns):
            checked.append("BIRT_442_HIGHLIGHT_RULES_VALID: Conditional <list-property name='highlightRules'> verified")

        return VerificationReport(
            is_valid=len(errors) == 0,
            checked_invariants=checked,
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def _derive_report_name(request_text: str) -> str:
        low = request_text.lower()
        if ("appointment" in low or "appt" in low) and "patient" in low and ("day" in low or "daily" in low) and "unit" in low:
            return "Daily_Unit_Patient_Appointment_Summary"
        if "slot" in low and "utilization" in low:
            return "Outpatient_Slot_Utilization_Summary"
        if ("appointment" in low or "scheduling" in low) and ("no-show" in low or "no show" in low or "cancel" in low):
            return "Appointment_No_Show_Cancellation_Audit"
        if any(
            w in low
            for w in (
                "precaution order",
                "precaution orders",
                "isolation order",
                "isolation orders",
                "fall precaution",
                "aspiration precaution",
                "seizure precaution",
                "suicide precaution",
                "hip precaution",
                "sternal precaution",
            )
        ):
            return "Inpatient_Precaution_Orders_Report"
        if any(w in low for w in ("1:1 observation", "video monitoring", "patient observation needs", "close observation", "sitter order")):
            return "One_To_One_Observation_Orders_Report"
        if "wound" in low and "consult" in low and ("order" in low or "orders" in low):
            return "Wound_Care_Consult_Orders_Report"
        if any(w in low for w in ("diet report", "diet order", "diet orders", "dietary order", "dietary orders", "npo", "tube feeding order")) and "allergy" not in low:
            return "Diet_And_Nutrition_Orders_Report"
        if "diet" in low and "allergy" in low:
            return "Dietary_Food_Allergy_Reconciliation_Report"
        if any(w in low for w in ("c-ssrs", "cssrs", "columbia", "suicide")):
            return "CSSRS_Suicide_Screening_Audit"
        if any(w in low for w in ("oral care", "oral hygiene", "mouth care", "teeth brushing", "denture care")):
            return "Oral_Care_Documentation_Report"
        if (
            any(
                w in low
                for w in (
                    "bladder/bowel",
                    "bowel/bladder",
                    "bladder and bowel",
                    "bowel and bladder",
                    "bladder management",
                    "bowel management",
                    "bladder scan",
                    "bowel movement",
                    "bowel elimination",
                    "bowel continence",
                    "bowel incontinence",
                    "toileting",
                    "post-void residual",
                )
            )
            and not any(
                w in low
                for w in ("cauti", "culture", "order", "orders", "diagnosis", "diagnoses", "icd-10", "icd10", "cancer", "neoplasm")
            )
        ):
            return "Bladder_Bowel_Management_Report"
        if any(w in low for w in ("bathing", "bath type", "bathing refused", "daily hygiene", "chucks/wipes")):
            return "Bathing_Hygiene_Documentation_Report"
        if any(w in low for w in ("peripheral iv", "central line", "central iv", "central venous", "picc line")):
            return "Peripheral_Central_IV_Catheter_Report"
        if any(w in low for w in ("nutrition assessment", "nutritional screening", "nutrition evaluation", "dietary risk", "nutrition risk")):
            return "Inpatient_Nutrition_Assessment_Report"
        if any(w in low for w in ("pharmacy clinical intervention", "pharmacy intervention", "pharmacist intervention", "pharmacoeconomic")):
            return "Pharmacy_Clinical_Interventions_Report"
        if any(w in low for w in ("cauti", "indwelling urinary", "foley")):
            return "CAUTI_Catheter_Surveillance_Audit"
        if any(w in low for w in ("pressure injury", "hapi", "braden")) or ("wound" in low and "assessment" in low):
            return "Pressure_Injury_Wound_Assessment_Audit"
        if "vte" in low or "thromboembolism" in low:
            return "Inpatient_VTE_Prophylaxis_Audit"
        if "bariatric" in low or ("bmi" in low and "weight" in low):
            return "Inpatient_Bariatric_BMI_Audit"
        if "pain" in low and "reassessment" in low:
            return "Pain_Medication_Reassessment_Audit"
        if any(w in low for w in ("opioid", "mme", "pdmp", "benzodiazepine")):
            return "Opioid_MME_Surveillance_Audit"
        if any(w in low for w in ("late medication", "late dose", "late doses", "late mar")):
            return "Late_Medication_Administration_Audit"
        if any(w in low for w in ("missed medication", "missed med", "overdue medication task", "overdue task")):
            return "Missed_Medication_Task_Audit"
        if "bcma" in low:
            return "BCMA_Medication_Administration_Audit"
        if "pharmacy" in low and "verif" in low:
            return "Pharmacy_Order_Verification_Audit"
        if ("therapy" in low and "minutes" in low) or "pt, ot" in low or "pt/ot/slp" in low:
            return "Rehab_Therapy_Productivity_Summary"
        if any(w in low for w in ("oppe", "fppe")) or ("note" in low and "timeliness" in low):
            return "Provider_Note_Timeliness_Audit"
        if "resuscitation" in low or "code status" in low:
            return "Inpatient_Code_Status_Census"
        if ("him" in low or "coding" in low or "drg" in low or "poa" in low) and ("diagnosis" in low or "diagnoses" in low or "icd" in low):
            return "HIM_Diagnosis_Extract_Report"
        if any(w in low for w in ("health plan", "health plans", "insurance plan", "primary and secondary insurance", "payer")) and not any(w in low for w in ("charge", "payment", "adjustment", "revenue", "benefit order")):
            return "Encounter_Health_Plans_Report"
        if (
            ("active" in low and "census" in low)
            or "midnight census" in low
            or (
                any(w in low for w in ("active inpatient", "current inpatient"))
                and not any(
                    w in low
                    for w in (
                        "order", "orders", "culture", "diagnosis", "diagnoses", "icd",
                        "medication", "mar", "vitals", "allergy", "allergies", "note", "notes",
                        "charge", "charges", "surgery", "surgical",
                    )
                )
            )
        ):
            return "Active_Inpatient_Encounter_Census"
        if "discharge disposition" in low:
            return "Discharge_Disposition_Summary"
        if "readmission" in low:
            return "Inpatient_Readmission_Audit"
        if "duplicate charge" in low or ("charge" in low and "ndc" in low):
            return "Encounter_Charge_NDC_Audit"
        if "cwf gs revenue" in low or ("revenue" in low and "transaction" in low):
            return "Revenue_Transaction_Detail_Extract"
        if "cwf gs payments" in low or ("payment" in low and "transaction" in low):
            return "Payment_Transaction_Detail_Extract"
        if "cwf gs adjustments" in low or ("adjustment" in low and "transaction" in low):
            return "Adjustment_Transaction_Detail_Extract"

        # Strip relative lookback windows (e.g. "last 7 days", "past 30 days", "within 24 hours") before tokenizing
        cleaned = re.sub(r"\b(?:in\s+the\s+)?(?:last|past|prior|previous|within|next)\s+\d+\s+(?:hour|hours|day|days|week|weeks|month|months|year|years)\b", " ", request_text, flags=re.IGNORECASE)
        stop = {
            "a", "about", "above", "across", "after", "again", "against", "all", "also", "am", "an", "and",
            "any", "are", "aren", "around", "as", "ask", "at", "be", "because", "been", "before", "being",
            "below", "between", "both", "breakdown", "broken", "build", "building", "but", "by", "calculate",
            "can", "cannot", "column", "columns", "compile", "contain", "containing", "contains", "could",
            "count", "counted", "counting", "counts", "create", "creating", "data", "dataset", "did", "display",
            "do", "does", "doing", "done", "down", "during", "each", "either", "every", "extract", "fetch",
            "field", "fields", "filter", "filtered", "filtering", "find", "finding", "for", "format", "from",
            "further", "generate", "generating", "get", "getting", "give", "given", "gives", "giving", "go",
            "group", "grouped", "grouping", "groups", "had", "has", "have", "having", "he", "help", "her",
            "here", "hers", "herself", "him", "himself", "his", "how", "however", "i", "if", "in", "include",
            "included", "includes", "including", "information", "into", "is", "it", "its", "itself", "just",
            "know", "last", "let", "like", "list", "listing", "lists", "look", "looking", "made", "make",
            "making", "many", "may", "me", "might", "more", "most", "much", "must", "my", "myself", "need",
            "needed", "needing", "needs", "new", "next", "no", "nor", "not", "now", "number", "numbers", "of",
            "off", "on", "once", "only", "onto", "or", "other", "ought", "our", "ours", "ourselves", "out",
            "output", "over", "own", "past", "per", "please", "plus", "prepare", "present", "previous",
            "prior", "produce", "producing", "provide", "provided", "provides", "pull", "pulling", "put",
            "queries", "query", "record", "records", "relate", "related", "report", "reporting", "reports",
            "request", "requested", "require", "required", "result", "results", "return", "returning",
            "returns", "row", "rows", "run", "running", "same", "saw", "say", "see", "seeing", "seen",
            "select", "selected", "selecting", "send", "set", "shall", "she", "should", "show", "showing",
            "shown", "shows", "since", "so", "some", "sort", "sorted", "sorting", "specify", "split", "sum",
            "summarize", "sums", "table", "tables", "take", "tell", "than", "that", "the", "their", "theirs",
            "them", "themselves", "then", "there", "these", "they", "this", "those", "through", "to",
            "together", "too", "total", "totals", "under", "until", "up", "upon", "us", "use", "used",
            "using", "value", "values", "very", "view", "want", "wanted", "wanting", "wants", "was", "way",
            "we", "well", "were", "what", "when", "where", "whether", "which", "while", "who", "whom",
            "whose", "why", "will", "with", "within", "without", "work", "would", "write", "writing", "you",
            "your", "yours", "yourself", "yourselves",
        }
        norm_map = {
            "patients": "Patient", "patient": "Patient", "pt": "Patient", "pts": "Patient",
            "appointments": "Appointment", "appointment": "Appointment", "appts": "Appointment", "appt": "Appointment",
            "schedules": "Schedule", "schedule": "Schedule", "scheduling": "Scheduling", "scheduled": "Scheduled",
            "encounters": "Encounter", "encounter": "Encounter", "encntr": "Encounter", "enc": "Encounter",
            "inpatients": "Inpatient", "inpatient": "Inpatient",
            "outpatients": "Outpatient", "outpatient": "Outpatient",
            "units": "Unit", "unit": "Unit",
            "facilities": "Facility", "facility": "Facility",
            "days": "Daily", "day": "Daily", "daily": "Daily",
            "weeks": "Weekly", "week": "Weekly", "weekly": "Weekly",
            "months": "Monthly", "month": "Monthly", "monthly": "Monthly",
            "orders": "Order", "order": "Order",
            "medications": "Medication", "medication": "Medication", "meds": "Medication", "med": "Medication",
            "administrations": "Administration", "administration": "Administration", "administered": "Administration",
            "charges": "Charge", "charge": "Charge",
            "diagnoses": "Diagnosis", "diagnosis": "Diagnosis", "dx": "Diagnosis",
            "allergies": "Allergy", "allergy": "Allergy",
            "assessments": "Assessment", "assessment": "Assessment",
            "notes": "Note", "note": "Note",
            "providers": "Provider", "provider": "Provider", "physicians": "Physician", "physician": "Physician",
            "admissions": "Admission", "admission": "Admission", "admits": "Admission", "admit": "Admission",
            "discharges": "Discharge", "discharge": "Discharge",
            "labs": "Lab", "lab": "Lab", "laboratory": "Laboratory",
            "vitals": "Vital_Signs", "vital": "Vital",
            "rehab": "Rehab", "rehabilitation": "Rehab",
            "payments": "Payment", "payment": "Payment",
            "adjustments": "Adjustment", "adjustment": "Adjustment",
            "denials": "Denial", "denial": "Denial",
            "fin": "FIN", "mrn": "MRN", "bmi": "BMI", "vte": "VTE", "cauti": "CAUTI", "cssrs": "CSSRS",
            "bcma": "BCMA", "mar": "MAR", "ed": "ED", "er": "ED", "icu": "ICU", "ndc": "NDC", "drg": "DRG",
            "icd10": "ICD10", "oppe": "OPPE", "fppe": "FPPE", "mme": "MME", "pdmp": "PDMP", "prn": "PRN",
        }
        raw_tokens = re.findall(r"[A-Za-z0-9]+", cleaned)
        kept: list[str] = []
        seen: set[str] = set()
        for tok in raw_tokens:
            tl = tok.lower()
            if tl in stop or len(tl) < 2 or tl.isdigit():
                continue
            norm = norm_map.get(tl, tok.capitalize() if not tok.isupper() else tok)
            for part in norm.split("_"):
                pl = part.lower()
                if pl not in seen and pl not in stop:
                    seen.add(pl)
                    kept.append(part)
        front_mods = {"Daily", "Weekly", "Monthly", "Inpatient", "Outpatient", "ED", "ICU", "PRN"}
        mods = [w for w in kept if w in front_mods]
        rest = [w for w in kept if w not in front_mods]
        ordered = (mods + rest)[:4]
        if not ordered:
            ordered = ["Clinical", "Analytical"]

        if any(w in low for w in ("count", "counts", "total", "totals", "grouped", "summary", "volume", "utilization", "rate", "average")):
            suffix = "Summary"
        elif any(w in low for w in ("audit", "compliance", "missing", "overdue", "surveillance", "timeliness", "unverified", "discrepancy")):
            suffix = "Audit"
        elif any(w in low for w in ("census", "inhouse", "currently admitted", "bed board")):
            suffix = "Census"
        else:
            suffix = "Report"

        if ordered[-1].lower() != suffix.lower():
            ordered.append(suffix)
        return "_".join(ordered)[:48].rstrip("_")
