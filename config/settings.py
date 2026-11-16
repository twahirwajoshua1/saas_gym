"""
Settings for the Gym Business Operating System.

Anything that differs between machines (passwords, the secret key, debug mode)
is read from a file called `.env`, which is never saved to Git.
See `.env.example` for the values that are needed.
"""

from pathlib import Path

import environ

# The project's top folder (the one that contains manage.py).
BASE_DIR = Path(__file__).resolve().parent.parent

# Read the .env file if it exists.
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")


# --- Security ---------------------------------------------------------------

# Used by Django to sign sessions and tokens. Must be secret in production.
SECRET_KEY = env("SECRET_KEY")

# Debug mode shows detailed error pages. It must be False in production.
DEBUG = env.bool("DEBUG", default=False)

# The web addresses this application is allowed to answer on.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])


# --- Applications -----------------------------------------------------------

# Django's own building blocks.
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

# Our own modules. Each of the nine applications will be added here
# as its own entry when it is built.
LOCAL_APPS = [
    "apps.core",
    "apps.people",
    "apps.accounts",
    "apps.audit",
    "apps.gyms",
    "apps.modules",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Runs each request as the restricted database role (tenant isolation, lock 2).
    "apps.core.middleware.DatabaseRoleMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# --- Database ---------------------------------------------------------------

# PostgreSQL only. The address, name, user and password come from DATABASE_URL
# in the .env file, for example:
#   postgres://gymos:password@localhost:5432/gymos
DATABASES = {
    "default": env.db("DATABASE_URL"),
}


# --- Login ------------------------------------------------------------------

# Our own user model: people sign in with a phone number.
AUTH_USER_MODEL = "accounts.User"

# Checks the phone number and password, and locks a number after too many
# wrong attempts. Used by our sign-in page and by the platform admin.
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.ThrottledPhoneBackend"]

# Where to send someone who must sign in first, and where they go afterwards.
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "gyms:enter"


# --- Passwords --------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- Language and time ------------------------------------------------------

LANGUAGE_CODE = "en"

# Times are stored in the database in UTC and shown to users in Kigali time.
TIME_ZONE = "Africa/Kigali"
USE_I18N = True
USE_TZ = True


# --- Static files (CSS, JavaScript, images) ---------------------------------

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
