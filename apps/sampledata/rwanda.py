"""
Ingredients for realistic Rwandan sample data.

Everything here is invented. Any resemblance of a gym name to a real business
is unintended. Places are real Rwandan neighbourhoods and towns.
"""

FEMALE_FIRST_NAMES = [
    "Aline",
    "Diane",
    "Grace",
    "Clarisse",
    "Josiane",
    "Divine",
    "Yvette",
    "Sandrine",
    "Claudine",
    "Chantal",
    "Solange",
    "Ange",
    "Esther",
    "Alice",
    "Liliane",
    "Vestine",
    "Jeannette",
    "Odette",
    "Immaculée",
    "Belyse",
    "Gisèle",
    "Aimée",
    "Nadine",
    "Sonia",
    "Carine",
    "Fiona",
    "Teta",
    "Keza",
    "Ineza",
    "Isimbi",
    "Gentille",
    "Nadia",
    "Joselyne",
    "Diana",
    "Ruth",
    "Sylvie",
    "Annick",
    "Clémence",
    "Mireille",
    "Uwase",
]
MALE_FIRST_NAMES = [
    "Eric",
    "Patrick",
    "Olivier",
    "Emmanuel",
    "Jean Claude",
    "Jean Paul",
    "Fabrice",
    "Didier",
    "Kevin",
    "Brian",
    "Thierry",
    "Innocent",
    "Théogène",
    "Alphonse",
    "Faustin",
    "Gilbert",
    "Samuel",
    "Moïse",
    "Elie",
    "Yves",
    "Aimable",
    "Bosco",
    "Jean de Dieu",
    "Claude",
    "Christian",
    "Arsène",
    "Cédric",
    "Pacifique",
    "Gad",
    "Shema",
    "Ganza",
    "Hervé",
    "Jules",
    "Jonathan",
    "Fiston",
    "Serge",
    "Prince",
    "David",
    "Joseph",
    "Daniel",
]
SURNAMES = [
    "Mugisha",
    "Niyonzima",
    "Habimana",
    "Uwase",
    "Ingabire",
    "Uwimana",
    "Nshimiyimana",
    "Mukamana",
    "Hakizimana",
    "Iradukunda",
    "Ishimwe",
    "Kayitesi",
    "Ndayisaba",
    "Nkurunziza",
    "Tuyishime",
    "Umutoni",
    "Uwera",
    "Bizimana",
    "Munyaneza",
    "Ntwari",
    "Gatete",
    "Rukundo",
    "Murenzi",
    "Mutesi",
    "Kamanzi",
    "Karangwa",
    "Nyiramana",
    "Mukeshimana",
    "Uwamahoro",
    "Nsengiyumva",
    "Niyitegeka",
    "Twagirimana",
    "Habiyaremye",
    "Uwizeye",
    "Manzi",
    "Mugabo",
    "Mbabazi",
    "Gasana",
    "Rutayisire",
    "Kalisa",
    "Nzeyimana",
    "Uwineza",
    "Byiringiro",
    "Irakoze",
    "Mutoni",
    "Nirere",
    "Uwamariya",
    "Habumugisha",
    "Ndahiro",
    "Sibomana",
    "Tuyisenge",
    "Mugwaneza",
    "Hategekimana",
    "Niyonsaba",
    "Umuhoza",
    "Nyirahabimana",
    "Rwagasana",
    "Kabera",
    "Mutabazi",
    "Ruzindana",
]

# Surnames with these beginnings are only given to women.
FEMALE_ONLY_PREFIXES = ("Nyira", "Muka")

# MTN Rwanda numbers start 078 or 079, Airtel 072 or 073.
PHONE_PREFIXES = ["78", "78", "78", "79", "79", "72", "73"]

GYMS = [
    {
        "name": "Imbaraga Fitness Studio",
        "owner": ("Diane", "Uwamahoro", "female"),
        "owner_phone": "0788100001",
        "branches": ["Kimihurura"],
        "members": 45,
        "packages": [
            ("Daily pass", 2000, 1, "day"),
            ("Weekly", 8000, 1, "week"),
            ("Monthly", 20000, 1, "month"),
            ("Student monthly", 15000, 1, "month"),
        ],
    },
    {
        "name": "Ubuzima Health Club",
        "owner": ("Patrick", "Rutayisire", "male"),
        "owner_phone": "0788100002",
        "branches": ["Kacyiru", "Remera"],
        "members": 90,
        "packages": [
            ("Daily pass", 3000, 1, "day"),
            ("Monthly", 30000, 1, "month"),
            ("Quarterly", 80000, 3, "month"),
            ("Student monthly", 20000, 1, "month"),
        ],
    },
    {
        "name": "Intare Strength House",
        "owner": ("Jean Paul", "Gasana", "male"),
        "owner_phone": "0788100003",
        "branches": ["Kicukiro", "Nyamirambo", "Gisozi"],
        "members": 135,
        "packages": [
            ("Daily pass", 3000, 1, "day"),
            ("Monthly", 35000, 1, "month"),
            ("Quarterly", 95000, 3, "month"),
            ("Annual", 330000, 1, "year"),
        ],
    },
    {
        "name": "Umucyo Wellness Centre",
        "owner": ("Clarisse", "Mbabazi", "female"),
        "owner_phone": "0788100004",
        "branches": ["Nyarutarama", "Kimironko", "Kibagabaga", "Gikondo"],
        "members": 180,
        "packages": [
            ("Daily pass", 5000, 1, "day"),
            ("Monthly", 50000, 1, "month"),
            ("Quarterly", 135000, 3, "month"),
            ("Annual", 480000, 1, "year"),
        ],
    },
    {
        "name": "Inkingi Fitness Network",
        "owner": ("Emmanuel", "Kalisa", "male"),
        "owner_phone": "0788100005",
        "branches": ["Remera", "Kacyiru", "Musanze", "Huye", "Rubavu"],
        "members": 225,
        "packages": [
            ("Daily pass", 4000, 1, "day"),
            ("Monthly", 40000, 1, "month"),
            ("Quarterly", 110000, 3, "month"),
            ("Annual", 400000, 1, "year"),
            ("Student monthly", 25000, 1, "month"),
        ],
    },
]

PLATFORM_ADMIN_PHONE = "0788100000"

FREEZE_REASONS = [
    "Travelling for work",
    "Visiting family upcountry",
    "Recovering from a knee injury",
    "Exams",
    "Doctor's advice to rest",
]

# Single sessions each gym sells: (name, price for members). The prices rise
# with the gym's tier, like the packages. Not every gym has a pool.
SERVICES = {
    "Imbaraga Fitness Studio": [("Gym", 2000), ("Aerobics", 2500)],
    "Ubuzima Health Club": [("Gym", 3000), ("Sauna", 5000), ("Aerobics", 3000)],
    "Intare Strength House": [("Gym", 3000), ("Sauna", 5000), ("Massage", 15000)],
    "Umucyo Wellness Centre": [
        ("Gym", 5000),
        ("Sauna", 7000),
        ("Massage", 20000),
        ("Swimming", 6000),
        ("Aerobics", 5000),
    ],
    "Inkingi Fitness Network": [("Gym", 4000), ("Sauna", 6000), ("Swimming", 5000)],
}

# The kinds of massage a gym offers: (name, minutes, share of the base price
# in thousandths, what to expect). The share keeps the prices sensible whatever
# the gym's tier: a Swedish rub-down is cheaper than hot stones.
MASSAGE_TYPES = [
    ("Swedish", 30, 600, "A gentle all-over massage to loosen tired muscles."),
    ("Deep tissue", 60, 1000, "Firm pressure on the knots. Say if it is too much."),
    ("Sports recovery", 45, 900, "For after a hard session or a race."),
    ("Hot stone", 60, 1250, "Warm stones along the back. Allow ten minutes to settle."),
]

# Invented employers with corporate wellness contracts. Any resemblance to a
# real company is unintended. Contract prices are a share of the member price;
# branches outside Kigali are cheaper.
# Employers with corporate wellness contracts. These are real Rwandan
# institutions, used by name because the founder asked for them, so pilot gyms
# recognise their world. Every person, number and visit is invented
# (demonstration data). Contract prices are a share of the member price;
# branches outside Kigali are cheaper.
COMPANIES = [
    {
        "name": "WASAC",
        "tin": "",
        "officer": ("Aimée", "Ingabire"),
        "officer_phone": "0788100010",
        "employees": 40,
        "gyms": ["Ubuzima Health Club", "Umucyo Wellness Centre"],
        "discount": 0.8,
    },
    {
        "name": "REG",
        "tin": "",
        "officer": ("Olivier", "Nshimiyimana"),
        "officer_phone": "0788100011",
        "employees": 55,
        "gyms": ["Umucyo Wellness Centre", "Inkingi Fitness Network", "Intare Strength House"],
        "discount": 0.8,
    },
    {
        "name": "RTDA",
        "tin": "",
        "officer": ("Claudine", "Mukeshimana"),
        "officer_phone": "0788100012",
        "employees": 25,
        "gyms": ["Intare Strength House", "Umucyo Wellness Centre"],
        "discount": 0.85,
    },
    {
        "name": "REMA",
        "tin": "",
        "officer": ("Jean Paul", "Habimana"),
        "officer_phone": "0788100013",
        "employees": 15,
        "gyms": ["Umucyo Wellness Centre"],
        "discount": 0.85,
    },
    {
        "name": "MINAGRI",
        "tin": "",
        "officer": ("Vestine", "Uwamahoro"),
        "officer_phone": "0788100014",
        "employees": 30,
        "gyms": ["Inkingi Fitness Network", "Umucyo Wellness Centre"],
        "discount": 0.85,
    },
]
OUTSIDE_KIGALI = {"Musanze", "Huye", "Rubavu"}
DEPARTMENTS = ["Finance", "Operations", "Sales", "Human Resources", "IT", "Claims", "Logistics"]


# What the sample gyms teach: name, what to expect, minutes, places.
CLASSES = [
    (
        "Aerobics",
        "An hour of movement to music, from easy to hard. Bring water and a towel. "
        "Good for anyone, whatever shape you are in.",
        60,
        25,
    ),
    (
        "Spinning",
        "Indoor cycling with the lights down and the music up. Hard work for the legs "
        "and the lungs. Arrive early to set your saddle.",
        45,
        18,
    ),
    (
        "Body conditioning",
        "Weights, bands and your own body weight, in rounds. The trainer shows every "
        "move first, so beginners are welcome.",
        60,
        20,
    ),
    (
        "Zumba",
        "Dance rather than drill. No experience needed and nobody is watching you.",
        60,
        30,
    ),
    (
        "Yoga",
        "Slow, quiet stretching and breathing. Mats are here; wear something you can move in.",
        60,
        16,
    ),
]

# The weekly pattern: class, weekday (0 = Monday), hour.
TIMETABLE = [
    ("Aerobics", 1, 18),
    ("Aerobics", 3, 18),
    ("Spinning", 0, 6),
    ("Spinning", 4, 18),
    ("Body conditioning", 2, 17),
    ("Zumba", 5, 10),
    ("Yoga", 6, 9),
]

# How trainers describe themselves. Invented people, invented words.
TRAINER_WORDS = [
    (
        "Strength and conditioning",
        "Ten years on the floor. I will fix your form before I add any weight, and I "
        "would rather you came three times a week for a year than once for an hour.",
    ),
    (
        "Classes and cardio",
        "I take the loud classes. Come as you are; nobody here started fit.",
    ),
    (
        "Beginners and coming back from injury",
        "I work with people starting again after a break, an illness or a baby. We "
        "start where you are, not where a magazine says you should be.",
    ),
    (
        "Weight training",
        "Barbells, patience and good records. If you want to get stronger and you can "
        "turn up, I can help.",
    ),
]
