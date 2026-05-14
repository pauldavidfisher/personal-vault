#!/usr/bin/env python3
"""
raindrop_ddc.py — Add Dewey Decimal Classification as a parallel layer
to a Raindrop.io bookmark export CSV.

Reads:  export.csv  (Raindrop export)
Writes: export_ddc.csv  (same data + ddc_code and ddc_label columns)
        ddc_report.txt  (summary of classifications)

Usage:
    python3 raindrop_ddc.py
    python3 raindrop_ddc.py --input export.csv --output export_ddc.csv
    python3 raindrop_ddc.py --report   # show classification summary only
    python3 raindrop_ddc.py --unmatched  # show tags with no DDC mapping

The DDC tag added to each bookmark is formatted as:
    ddc:641.5 | Cooking by method

This can be imported back into Raindrop as a new tag,
sitting alongside your existing tags unchanged.
"""

import csv
import re
import sys
import argparse
from pathlib import Path
from collections import Counter, defaultdict
from urllib.parse import urlparse


# ── DDC Taxonomy ──────────────────────────────────────────────────────────────
# Maps DDC code → human label
DDC_LABELS = {
    # 000s — Computer Science, Information & General Works
    '000': 'Computer science & general works',
    '005': 'Computer programming & software',
    '006': 'Special computer methods',
    '020': 'Library & information sciences',
    '050': 'Magazines & serials',
    '060': 'Associations & organizations',
    '070': 'News media, journalism & publishing',
    '080': 'General collections',

    # 100s — Philosophy & Psychology
    '100': 'Philosophy',
    '120': 'Epistemology',
    '130': 'Parapsychology & occult',
    '150': 'Psychology',
    '160': 'Logic',
    '170': 'Ethics & moral philosophy',
    '180': 'Ancient & medieval philosophy',
    '190': 'Modern western philosophy',

    # 200s — Religion
    '200': 'Religion',
    '210': 'Natural theology',
    '220': 'Bible',
    '230': 'Christianity & Christian theology',
    '240': 'Christian practice & observance',
    '250': 'Christian pastoral practice',
    '260': 'Christian organization & social work',
    '270': 'History of Christianity',
    '280': 'Christian denominations',
    '290': 'Other & comparative religions',

    # 300s — Social Sciences
    '300': 'Social sciences',
    '310': 'Statistics',
    '320': 'Political science',
    '321': 'Systems of governments',
    '322': 'Church & state',
    '323': 'Civil & political rights',
    '324': 'The political process',
    '325': 'International migration & colonization',
    '327': 'International relations',
    '330': 'Economics',
    '332': 'Financial economics',
    '333': 'Land economics & energy',
    '336': 'Public finance',
    '338': 'Production',
    '340': 'Law',
    '346': 'Private law',
    '347': 'Civil procedure',
    '350': 'Public administration',
    '360': 'Social problems & services',
    '362': 'Social welfare & criminology',
    '370': 'Education',
    '380': 'Commerce, communications & transportation',
    '381': 'Commerce',
    '382': 'International commerce',
    '383': 'Postal communications',
    '384': 'Communications & telecommunication',
    '385': 'Railroad transportation',
    '386': 'Inland waterway & ferry transportation',
    '387': 'Water, air & space transportation',
    '388': 'Ground transportation',
    '390': 'Customs, etiquette & folklore',
    '391': 'Costume & personal appearance',
    '394': 'General customs',
    '398': 'Folklore',

    # 400s — Language
    '400': 'Language',
    '410': 'Linguistics',
    '420': 'English',
    '430': 'German',
    '440': 'French',
    '450': 'Italian',
    '460': 'Spanish & Portuguese',
    '470': 'Latin',
    '480': 'Classical Greek',
    '490': 'Other languages',

    # 500s — Science
    '500': 'Science',
    '510': 'Mathematics',
    '515': 'Analysis & calculus',
    '519': 'Probabilities & applied mathematics',
    '520': 'Astronomy',
    '530': 'Physics',
    '540': 'Chemistry',
    '550': 'Earth sciences',
    '560': 'Paleontology',
    '570': 'Biology & life sciences',
    '580': 'Plants',
    '590': 'Animals',

    # 600s — Technology
    '600': 'Technology',
    '610': 'Medicine & health',
    '611': 'Human anatomy',
    '612': 'Human physiology',
    '613': 'Personal health & safety',
    '614': 'Public health',
    '615': 'Pharmacology & therapeutics',
    '620': 'Engineering',
    '621': 'Applied physics',
    '624': 'Civil engineering',
    '625': 'Railroad & road engineering',
    '629': 'Other branches of engineering',
    '630': 'Agriculture',
    '635': 'Garden crops & horticulture',
    '636': 'Animal husbandry',
    '640': 'Home & family management',
    '641': 'Food & drink',
    '641.3': 'Food',
    '641.4': 'Food preservation & storage',
    '641.5': 'Cooking',
    '641.51': 'Cooking by method — slow cooker',
    '641.52': 'Cooking by method — grilling & BBQ',
    '641.53': 'Cooking by method — baking',
    '641.54': 'Cooking by method — air fryer',
    '641.55': 'Cooking by method — one pot & pan',
    '641.56': 'Cooking — breakfast',
    '641.57': 'Cooking — soup',
    '641.58': 'Cooking — braised & roasted',
    '641.6': 'Cooking specific ingredients',
    '641.61': 'Cooking — beef',
    '641.62': 'Cooking — pork',
    '641.63': 'Cooking — chicken & poultry',
    '641.64': 'Cooking — fish & seafood',
    '641.65': 'Cooking — beans & legumes',
    '641.66': 'Cooking — smoked meats',
    '641.7': 'Cooking specific dishes',
    '641.71': 'Cooking — Italian',
    '641.72': 'Cooking — Mexican',
    '641.73': 'Cooking — Chinese',
    '641.74': 'Cooking — German',
    '641.75': 'Cooking — French',
    '641.76': 'Cooking — Greek',
    '641.77': 'Cooking — Middle Eastern',
    '641.78': 'Cooking — Korean',
    '641.8': 'Desserts, appetizers & sauces',
    '641.81': 'Cooking — appetizers & snacks',
    '641.82': 'Cooking — desserts & baking',
    '641.83': 'Cooking — sauces & dips',
    '641.84': 'Cooking — side dishes',
    '641.9': 'Cooking — lunch & dinner',
    '642': 'Meals & table service',
    '643': 'Housing & household equipment',
    '644': 'Household utilities',
    '645': 'Household furnishings',
    '646': 'Clothing & personal living',
    '647': 'Management of public households',
    '648': 'Housekeeping',
    '649': 'Child rearing',
    '650': 'Management & public relations',
    '651': 'Office services',
    '657': 'Accounting',
    '658': 'General management',
    '659': 'Advertising & public relations',
    '660': 'Chemical engineering',
    '670': 'Manufacturing',
    '680': 'Manufacture for specific uses',
    '690': 'Construction & building',
    '690.1': 'Construction — general',
    '690.2': 'Framing & structure',
    '690.3': 'Masonry, concrete & tile',
    '690.4': 'Plumbing',
    '690.5': 'Electrical',
    '690.6': 'Finish, millwork & carpentry',
    '690.61': 'Finish — railings & stairs',
    '690.62': 'Finish — drywall',
    '690.63': 'Finish — insulation',
    '690.64': 'Finish — mechanical (HVAC)',
    '690.65': 'Finish — tools',
    '690.7': 'Suppliers & sources',
    '690.71': 'Source — Home Depot',
    '690.72': 'Source — Lowes',
    '690.73': 'Source — Menards',
    '690.74': 'Source — IKEA',
    '690.75': 'Source — trade publications (JLC)',
    '690.8': 'Product research & hardware',
    '690.9': 'Construction projects',
    '690.91': 'Project — kitchen & bath',
    '690.911': 'Project — cabinets',
    '690.912': 'Project — appliances',
    '690.913': 'Project — shower',
    '690.914': 'Project — vanity & toilet',
    '690.92': 'Project — exterior',
    '690.921': 'Project — deck',
    '690.922': 'Project — columns & porch',
    '690.923': 'Project — siding',
    '690.924': 'Project — fence',
    '690.925': 'Project — garage',
    '690.93': 'Project — structure & roof',
    '690.94': 'Project — interior',
    '690.941': 'Project — floors',
    '690.942': 'Project — openings (doors & windows)',
    '690.943': 'Project — entryway',
    '690.95': 'Building codes & standards',
    '690.96': 'Business — estimating',
    '690.97': 'Project — Brian House',

    # 700s — Arts & Recreation
    '700': 'Arts & recreation',
    '710': 'Landscape & area planning',
    '720': 'Architecture',
    '721': 'Architectural structure',
    '722': 'Architecture — ancient',
    '724': 'Architecture — 19th & 20th century',
    '725': 'Public structures',
    '728': 'Residential buildings',
    '729': 'Design & decoration',
    '730': 'Plastic arts & sculpture',
    '740': 'Graphic arts & decorative arts',
    '741': 'Drawing & drawings',
    '743': 'Drawing by subject',
    '745': 'Decorative arts',
    '746': 'Textile arts',
    '747': 'Interior decoration',
    '748': 'Glass',
    '749': 'Furniture & accessories',
    '750': 'Painting',
    '760': 'Printmaking & prints',
    '770': 'Photography',
    '780': 'Music',
    '781': 'General principles & musical forms',
    '782': 'Vocal music',
    '784': 'Instruments & instrumental ensembles',
    '786': 'Keyboard instruments',
    '787': 'Stringed instruments',
    '788': 'Wind instruments',
    '790': 'Sports, games & entertainment',
    '791': 'Public performances',
    '792': 'Stage presentations',
    '793': 'Indoor games & amusements',
    '794': 'Indoor games of skill',
    '796': 'Athletic & outdoor sports',
    '797': 'Aquatic & air sports',
    '799': 'Fishing, hunting & shooting',

    # 800s — Literature
    '800': 'Literature',
    '810': 'American literature in English',
    '820': 'English literature',
    '830': 'German literature',
    '840': 'French literature',
    '850': 'Italian literature',
    '860': 'Spanish & Portuguese literature',
    '870': 'Latin literature',
    '880': 'Classical Greek literature',
    '890': 'Other literatures',

    # 900s — History & Geography
    '900': 'History & geography',
    '901': 'Philosophy & theory of history',
    '904': 'Collected accounts of events',
    '905': 'History — serials',
    '907': 'Education & research in history',
    '909': 'World history',
    '910': 'Geography & travel',
    '912': 'Atlases, maps & charts',
    '914': 'Geography of Europe',
    '916': 'Geography of Africa',
    '917': 'Geography of North America',
    '920': 'Biography',
    '929': 'Genealogy & insignia',
    '930': 'History of ancient world',
    '936': 'History of Germanic Europe',
    '937': 'History of Roman Empire',
    '938': 'History of ancient Greece',
    '940': 'History of Europe',
    '941': 'History of British Isles',
    '943': 'History of Germany',
    '944': 'History of France',
    '945': 'History of Italian Peninsula',
    '946': 'History of Iberian Peninsula',
    '947': 'History of Russia & Eastern Europe',
    '948': 'History of Scandinavia',
    '949': 'History of other parts of Europe',
    '950': 'History of Asia',
    '951': 'History of China',
    '952': 'History of Japan',
    '953': 'History of Arabian Peninsula',
    '956': 'History of Middle East',
    '960': 'History of Africa',
    '970': 'History of North America',
    '973': 'History of United States',
    '974': 'History of Northeastern US',
    '977': 'History of North Central US',
    '980': 'History of South America',
    '990': 'History of Australasia & Pacific',
}


# ── Tag → DDC mapping ─────────────────────────────────────────────────────────
# Each tag maps to a DDC code. Order matters for multi-tag resolution —
# more specific tags should be listed first in the per-bookmark logic.

TAG_TO_DDC = {
    # ── Source/platform tags (skip — metadata not subject) ──
    'IFTTT': None,
    'YouTube': None,
    'facebook': None,
    'instagram': None,
    'tiktok': None,
    'twitter': None,
    'x.com': None,
    'pinterest': None,
    'spotify': None,
    'flickr': None,
    'medium': None,
    'substack': None,
    'scribd': None,
    'codepen': None,
    'wikipedia': None,
    'wiki': None,
    'web': None,
    'text': None,
    'video': None,
    'picture': None,
    'gif': None,
    'psd': None,
    'document': None,
    'bookmarks': None,
    'archive': None,
    'threader': None,

    # ── People (Fisher family — personal/metadata) ──
    'paul': None,
    'natalie': None,
    'sophia': None,
    'chrisje': None,
    'tyler': None,
    'chloe': None,
    'luke': None,
    'david': None,
    'marcel': None,

    # ── 000s Computer Science ──
    'code': '005',
    'css': '005',
    'html': '005',
    'javascript': '005',
    'js': '005',
    'python': '005',
    'ruby': '005',
    'markdown': '005',
    'svg': '005',
    'animation': '005',
    'vector': '005',
    'generator': '005',
    'calculator': '005',
    'formula': '005',
    '2d': '005',
    'shapes': '005',
    'tutorial': '005',
    'web-tuts': '005',
    'bootstrap': '005',
    'isotope': '005',
    'mac': '005',
    'iphone': '005',
    'computer': '005',
    'bluehost': '005',
    'wordpress': '005',
    'chatgpt': '005',
    'code-editor': '005',
    'adobe': '005',
    'illustrator': '005',
    'photoshop': '005',
    'font': '005',
    'color': '005',
    'icon': '005',
    'texture': '005',
    'graphics': '005',
    'explorer': '006',
    'github': '005',
    'jekyll': '005',
    'local-repo': '005',
    'csv': '005',
    'excel': '005',
    'sketchup': '720',
    '3d': '006',
    'design-cad': '720',
    'tools': '005',
    'notes': '020',
    'grammar': '410',
    'words': '410',
    'latin': '470',
    'writing': '808',
    'definition': '410',

    # ── 100s Philosophy & Psychology ──
    'philosophy': '100',
    'ramblings': '100',
    'curious': '100',
    'essay': '100',
    'ethics': '170',
    'esoteric': '130',
    'mythology': '290',
    'anthropology': '301',

    # ── 200s Religion ──
    'bible': '220',
    'apologetics': '230',
    'religion': '200',
    'theology': '230',

    # ── 300s Social Sciences ──
    'politics': '320',
    'colonialism': '325',
    'corporation': '338',
    'law': '340',
    'charter': '340',
    'treaty': '341',
    'tax': '336',
    'finance': '332',
    'biz': '658',
    'marketing': '659',
    'affiliate': '381',
    'amazon': '381',
    'shipping': '387',
    'telecom': '384',
    'social': '302',
    'scandal': '364',
    'scandals': '364',
    'innovation': '338',
    'invention': '609',
    'trade': '382',
    'rental': '333',
    'juiceplus': '381',
    'doterra': '381',
    'office': '651',
    'accounts': '657',
    'email': '384',
    'sms': '384',
    'phone': '384',
    'google': '384',
    'contacts': '651',
    'events': '394',
    'classified ads': '381',

    # ── 370 Education ──
    'aadl': '020',

    # ── 390 Customs ──
    'culture': '390',
    'royals': '394',
    'clothes': '391',

    # ── 400s Language ──

    # ── 500s Science ──
    'science': '500',
    'math': '510',
    'covid': '614',
    'medical': '610',
    'health': '613',
    'nature': '500',
    'trees': '582',
    'oil': '553',
    'canals': '386',
    'aviation': '387',
    'maritime': '387',
    'steel': '670',

    # ── 600s Technology ──
    'repair': '643',
    'fix': '643',
    'diy': '643',
    'parts': '643',
    'auto-repair': '629',
    'automobile': '629',
    'car': '629',
    'appliance': '643',
    'storage': '648',
    'lights': '644',
    'lamp': '645',
    'furniture': '749',
    'home': '643',
    'manuals': '643',
    'non-food-recipe': '640',

    # ── Recipes — method ──
    'recipe': '641.5',
    'cooking': '641.5',
    'food': '641',
    'recipe-dinner': '641.9',
    'recipe-lunch': '641.9',
    'recipe-slow-cooker': '641.51',
    'recipe-bbq-grill': '641.52',
    'recipe-baking': '641.53',
    'recipe-air-fryer': '641.54',
    'recipe-one-pot-pan': '641.55',
    'recipe-breakfast': '641.56',
    'recipe-soup': '641.57',
    'recipe-braised': '641.58',
    'recipe-roast': '641.58',
    'recipe-smoked': '641.66',
    'recipe-fried': '641.5',

    # ── Recipes — ingredient ──
    'recipe-beef': '641.61',
    'recipe-pork': '641.62',
    'recipe-chicken': '641.63',
    'recipe-fish': '641.64',
    'recipe-seafood': '641.64',
    'recipe-shrimp': '641.64',
    'recipe-beans': '641.65',

    # ── Recipes — cuisine ──
    'recipe-italian': '641.71',
    'recipe-mexican': '641.72',
    'recipe-chinese': '641.73',
    'recipe-german': '641.74',
    'recipe-french': '641.75',
    'recipe-greek': '641.76',
    'recipe-middle-eastern': '641.77',
    'recipe-korean': '641.78',

    # ── Recipes — dish type ──
    'recipe-appetizer-snack': '641.81',
    'recipe-dessert': '641.82',
    'recipe-sauce-dip': '641.83',
    'recipe-side-dish': '641.84',

    # ── Recipe sources (subject = recipe, source is metadata) ──
    'recipe-source-seriouseats': '641.5',
    'recipe-source-mediterraneandish': '641.5',
    'recipe-source-allrecipes': '641.5',
    'recipe-source-foodandwine': '641.5',
    'recipe-source-pioneerwoman': '641.5',
    'recipe-source-thewoksoflife': '641.5',
    'recipe-source-simplyrecipes': '641.5',
    'recipe-source-nyt-cooking': '641.5',
    'recipe-source-foodnetwork': '641.5',
    'recipe-source-bonappetit': '641.5',
    'recipe-source-kingarthur': '641.53',
    'restaurant': '642',

    # ── Construction ──
    'con-project-kitchen-bath': '690.91',
    'con-project-kitchen-bath-cabinet': '690.911',
    'con-project-kitchen-bath-appliance': '690.912',
    'con-project-kitchen-bath-shower': '690.913',
    'con-project-kitchen-bath-vanity': '690.914',
    'con-project-kitchen-bath-toilet': '690.914',
    'con-project-exterior-deck': '690.921',
    'con-project-exterior-columns': '690.922',
    'con-project-exterior-porch': '690.922',
    'con-project-exterior-siding': '690.923',
    'con-project-exterior-fence': '690.924',
    'con-project-exterior-garage': '690.925',
    'con-project-structure-roof': '690.93',
    'con-project-structure': '690.93',
    'con-project-interior-floor': '690.941',
    'con-project-interior-openings': '690.942',
    'con-project-interior-entryway': '690.943',
    'con-trade-hardware': '690.8',
    'con-trade-masonry-tile': '690.3',
    'con-trade-plumbing': '690.4',
    'con-trade-electric': '690.5',
    'con-trade-carpentry': '690.6',
    'con-trade-carpentry-railing': '690.61',
    'con-trade-stairs': '690.61',
    'con-trade-stairs-railing': '690.61',
    'con-trade-drywall': '690.62',
    'con-trade-insulation': '690.63',
    'con-trade-mechanical': '690.64',
    'con-trade-tools': '690.65',
    'con-source-supplier': '690.7',
    'con-source-homedepot': '690.71',
    'con-source-lowes': '690.72',
    'con-source-menards': '690.73',
    'con-source-ikea': '690.74',
    'con-source-jlc-online': '690.75',
    'con-trade-framing': '690.2',
    'building-code': '690.95',
    'business-estimating': '690.96',
    'lumber': '690.2',
    'window': '690.942',
    'truss': '690.2',
    'azek': '690.6',
    'houzz': '729',

    # Brian House (future)
    'brian-house': '690.97',

    # ── 700s Arts & Recreation ──
    'design-arch': '720',
    'architecture': '720',
    'bungalow': '728',
    'eichler': '728',
    'house': '728',
    'arts-&-crafts': '745',
    'crafts': '745',
    'art': '700',
    'design': '745',
    'vintage': '745',
    'jewelry': '745',
    'furniture-design': '749',
    'animation-art': '741',
    'music': '780',
    'radio': '780',
    'tv': '791',
    'film': '791',
    'movies': '791',
    'youtube': '791',
    'dyk': '791',
    'photography': '770',
    'map': '912',
    'geography': '910',
    'travel': '910',
    'spain': '914',
    'barcelona': '914',
    'ann-arbor': '917',
    'a2': '917',
    'ypsi': '917',
    'aadl-lib': '020',

    # ── 800s Literature ──
    'book': '800',
    'literature': '800',
    'quote': '808',
    'letters': '820',
    'blogs': '070',

    # ── 900s History & Geography ──
    'history': '900',
    'biography': '920',
    'genealogy': '929',
    'rome': '937',
    'french-revolution': '944',
    'napoleon': '944',
    'ww2': '940',
    'war': '355',
    'china': '951',
    'colonialism-hist': '909',
    'world-fairs': '909',
    'jews': '909',
    'Jews': '909',
    'indian-trail': '970',
    'american-history': '973',

    # ── Plum Design Build (business) ──
    'plum-design-build': '690.96',
    'plum': '690.96',
    'sophiaruthfisher.com': '659',

    # ── Misc ──
    'google-maps': '912',
    'gutenberg': '020',
    'tax-filing': '336',
    'vacation': '910',
    'gadgets': '629',
    'print': '686',
    'social-media': '302',
    'meta': '302',
    'etsy': '381',
    'manuals-repair': '643',
    'engineering': '620',
    'family': '306',
    'revolution': '909',
    '.gov': '350',
}


# ── Priority: when multiple DDC codes apply, prefer the most specific ─────────
def most_specific_ddc(codes):
    """Return the most specific (longest) DDC code from a list."""
    if not codes:
        return None, None
    codes = [c for c in codes if c]
    if not codes:
        return None, None
    best = max(codes, key=lambda c: len(c))
    return best, DDC_LABELS.get(best, 'Uncategorized')


def classify_row(row):
    """
    Determine the best DDC code for a bookmark row.
    Returns (ddc_code, ddc_label) or ('', '') if no match.
    """
    tags = [t.strip() for t in row.get('tags', '').split(',') if t.strip()]
    url = row.get('url', '')
    domain = urlparse(url).netloc.replace('www.', '')

    # Collect all DDC codes from tags
    codes = []
    for tag in tags:
        code = TAG_TO_DDC.get(tag)
        if code:
            codes.append(code)

    # URL-based fallbacks for common domains
    if not codes:
        domain_map = {
            'youtube.com': '791',
            'youtu.be': '791',
            'github.com': '005',
            'stackoverflow.com': '005',
            'codepen.io': '005',
            'wikipedia.org': '900',
            'amazon.com': '381',
            'etsy.com': '381',
            'pinterest.com': '745',
            'houzz.com': '729',
            'lowes.com': '690.7',
            'homedepot.com': '690.7',
            'menards.com': '690.7',
            'ikea.com': '690.7',
            'jlconline.com': '690.75',
            'allrecipes.com': '641.5',
            'foodnetwork.com': '641.5',
            'seriouseats.com': '641.5',
            'simplyrecipes.com': '641.5',
            'thespruceeats.com': '641.5',
            'bonappetit.com': '641.5',
            'nytimes.com': '070',
            'theguardian.com': '070',
            'washingtonpost.com': '070',
            'substack.com': '070',
            'spotify.com': '780',
            'soundcloud.com': '780',
            'sketchup.com': '720',
            'squareup.com': '658',
            'fonts.google.com': '005',
            'flickr.com': '770',
            'instagram.com': '302',
            'facebook.com': '302',
        }
        for d, code in domain_map.items():
            if d in domain:
                codes.append(code)
                break

    code, label = most_specific_ddc(codes)
    return code or '', label or ''


# ── Report ────────────────────────────────────────────────────────────────────
def print_report(rows):
    code_counts = Counter()
    unmatched_tags = Counter()
    total = len(rows)
    matched = 0

    for row in rows:
        code = row.get('ddc_code', '')
        if code:
            matched += 1
            code_counts[code] += 1
        # Count unmatched tags
        for tag in row.get('tags', '').split(','):
            t = tag.strip()
            if t and t not in TAG_TO_DDC and TAG_TO_DDC.get(t) is not None:
                unmatched_tags[t] += 1

    print(f'\n{"═"*56}')
    print(f'  RAINDROP DDC CLASSIFICATION REPORT')
    print(f'{"═"*56}')
    print(f'  Total bookmarks:   {total:>6}')
    print(f'  Classified:        {matched:>6}  ({matched/total*100:.1f}%)')
    print(f'  Unclassified:      {total-matched:>6}')

    print(f'\n  Top DDC classes:')
    for code, count in code_counts.most_common(30):
        label = DDC_LABELS.get(code, '?')
        bar = '█' * min(25, count // 5)
        print(f'    {code:<8} {count:>5}  {label[:30]:<30}  {bar}')

    print(f'\n{"═"*56}\n')


def print_unmatched(rows):
    seen = set()
    unmatched = Counter()
    for row in rows:
        for tag in row.get('tags', '').split(','):
            t = tag.strip()
            if t and t not in TAG_TO_DDC:
                unmatched[t] += 1
    print(f'\n  Tags with NO DDC mapping ({len(unmatched)} unique):')
    for tag, count in sorted(unmatched.items(), key=lambda x: -x[1]):
        print(f'    {count:4}  {tag}')
    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Add DDC classification to Raindrop export')
    parser.add_argument('--input',     default='export.csv',     help='Input CSV (default: export.csv)')
    parser.add_argument('--output',    default='export_ddc.csv', help='Output CSV (default: export_ddc.csv)')
    parser.add_argument('--report',    action='store_true',       help='Show report only')
    parser.add_argument('--unmatched', action='store_true',       help='Show unmatched tags')
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f'❌ Input file not found: {input_path}')
        sys.exit(1)

    print(f'\n📚 Reading {input_path}...')
    rows = []
    with open(input_path, encoding='utf-8', errors='ignore', newline='') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            ddc_code, ddc_label = classify_row(row)
            row['ddc_code'] = ddc_code
            row['ddc_label'] = ddc_label
            # Add DDC as a parallel tag in the existing tags field
            # Format: ddc:641.5 appended to existing tags
            if ddc_code:
                existing = row['tags']
                ddc_tag = f'ddc:{ddc_code}'
                if ddc_tag not in existing:
                    row['tags_with_ddc'] = f'{existing}, {ddc_tag}' if existing else ddc_tag
                else:
                    row['tags_with_ddc'] = existing
            else:
                row['tags_with_ddc'] = row['tags']
            rows.append(row)

    print(f'   {len(rows)} bookmarks loaded')

    if args.unmatched:
        print_unmatched(rows)
        return

    print_report(rows)

    if args.report:
        return

    # Write output
    output_path = Path(args.output)
    out_fields = list(fieldnames) + ['ddc_code', 'ddc_label', 'tags_with_ddc']

    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

    print(f'✅ Written: {output_path}')
    print(f'   Columns added: ddc_code, ddc_label, tags_with_ddc')
    print(f'\n   To re-import into Raindrop:')
    print(f'   1. Use the "tags_with_ddc" column as your tags column')
    print(f'   2. Or import ddc_code / ddc_label as separate fields')
    print(f'   3. Your original tags column is unchanged\n')


if __name__ == '__main__':
    main()
