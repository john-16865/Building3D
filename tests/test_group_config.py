from building3d.config import load_group_config


def test_load_group_config_loads_science_members_and_aliases(tmp_path):
    config = tmp_path / "groups.yaml"
    config.write_text(
        """
groups:
  - id: science
    display_name: Science Centre
    members: ["301", "302", "303S"]
    aliases: ["science centre", "faculty of science"]
""",
        encoding="utf-8",
    )

    groups = load_group_config(config)
    science = groups.get("science centre")

    assert science.id == "science"
    assert science.display_name == "Science Centre"
    assert science.members == ["301", "302", "303S"]
    assert "301" in science.aliases
    assert "faculty of science" in science.aliases
