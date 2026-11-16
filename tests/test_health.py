"""Tests for the system status page."""

from unittest import mock

import pytest
from django.urls import reverse

from apps.core import services


@pytest.mark.django_db
def test_database_is_reachable():
    assert services.database_is_reachable() is True


@pytest.mark.django_db
def test_health_page_shows_connected(client):
    response = client.get(reverse("core:health"))

    assert response.status_code == 200
    assert "Database: <strong>connected</strong>" in response.content.decode()


def test_health_page_reports_a_database_failure(client):
    with mock.patch.object(services, "database_is_reachable", return_value=False):
        response = client.get(reverse("core:health"))

    assert response.status_code == 503
    assert "not reachable" in response.content.decode()
