from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class ComposeLoader(yaml.SafeLoader):
    pass


def _construct_compose_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


ComposeLoader.add_multi_constructor("!", _construct_compose_tag)


def test_compose_env_files_are_optional():
    compose_files = sorted(ROOT.glob("docker-compose*.yml"))

    for compose_file in compose_files:
        config = yaml.load(compose_file.read_text(encoding="utf-8"), Loader=ComposeLoader) or {}
        for service_name, service in config.get("services", {}).items():
            env_files = service.get("env_file", [])
            if isinstance(env_files, (str, dict)):
                env_files = [env_files]
            for entry in env_files:
                assert isinstance(entry, dict), (
                    f"{compose_file.name} service {service_name}: env_file must use long syntax"
                )
                assert entry.get("required") is False, (
                    f"{compose_file.name} service {service_name}: env_file must be optional"
                )
