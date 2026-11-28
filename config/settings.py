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

# Full addresses (with https://) that forms may be sent from, when the app is
# reached through another address, such as a server's domain or a test tunnel.
# Example: CSRF_TRUSTED_ORIGINS=https://gym.example.rw
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Set BEHIND_HTTPS_PROXY=True when a web server or tunnel in front of the app
# handles HTTPS. Then links the app builds (such as member card links) start
# with https://.
if env.bool("BEHIND_HTTPS_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


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
    "apps.members",
    "apps.team",
    "apps.billing",
    "apps.classes",
    "apps.massage",
    "apps.attendance",
    "apps.corporate",
    "apps.frontdesk",
    "apps.dashboard",
    "apps.spreadsheets",
    "apps.memberapp",
    "apps.platformadmin",
    # Only adds the create_sample_gyms command.
    "apps.sampledata",
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
    # Sends people whose password was set by someone else to choose their own.
    "apps.accounts.middleware.PasswordChangeRequiredMiddleware",
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
                # The current gym and the person's role, for navigation.
                "apps.gyms.context.current_gym",
                # Which applications the gym has switched on, for menus.
                "apps.modules.context.enabled_modules",
                # Whether a trainer gives massages, for the menu.
                "apps.classes.context.trainer_work",
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

# Short demo names (like "gymowner") on the sign-in page, for demonstrations on
# a developer's own computer. Needs DEBUG=True *and* DEMO_LOGINS=True in .env;
# it can never be on in a real installation. See create_demo_logins.
DEMO_LOGINS = DEBUG and env.bool("DEMO_LOGINS", default=False)

# Where to send someone who must sign in first, and where they go afterwards.
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "corporate:start"


# One-time notices such as "Payment recorded." are kept in the signed-in
# person's session, so signing out (or someone else signing in on the same
# computer) throws away any notice that was still waiting to be shown.
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"


# Uploads above this size are written to a temporary file instead of memory.
# The import itself refuses files above 5 MB.
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024


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

# Our own CSS and JavaScript files live in the project's static/ folder.
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
