"""Unit tests for the Roles enum helpers (resolve / names_str / get_name)."""

import pytest

from ipor_fusion.config.roles import Roles


class TestResolve:
    def test_canonical_name(self):
        assert Roles.resolve("ATOMIST_ROLE") == 100

    def test_case_insensitive(self):
        assert Roles.resolve("atomist_role") == 100
        assert Roles.resolve("Atomist") == 100

    def test_suffix_optional(self):
        assert Roles.resolve("owner") == 1
        assert Roles.resolve("OWNER") == 1

    def test_spaces_and_hyphens(self):
        assert Roles.resolve("ipor dao") == 4
        assert Roles.resolve("fuse-manager") == 300
        assert Roles.resolve("Ipor Dao Role") == 4

    def test_admin_is_zero(self):
        # 0 is a valid id — resolution must not treat it as falsy anywhere.
        assert Roles.resolve("admin") == 0

    def test_unknown_lists_valid_names(self):
        with pytest.raises(ValueError, match="Valid: ADMIN_ROLE"):
            Roles.resolve("archbishop")

    def test_near_miss_suggests(self):
        with pytest.raises(ValueError, match="Did you mean ATOMIST_ROLE"):
            Roles.resolve("atomsit")

    def test_blank_raises(self):
        with pytest.raises(ValueError, match="Unknown role ''"):
            Roles.resolve("")


class TestNamesStr:
    def test_contains_every_member(self):
        names_str = Roles.names_str()
        for role in Roles:
            assert role.name in names_str

    def test_comma_separated(self):
        assert Roles.names_str().startswith("ADMIN_ROLE, OWNER_ROLE")


class TestGetName:
    def test_known(self):
        assert Roles.get_name(100) == "ATOMIST_ROLE"

    def test_unknown_fallback(self):
        assert Roles.get_name(1234) == "UNKNOWN_ROLE_1234"
