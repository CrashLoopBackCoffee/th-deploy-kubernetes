import pulumi as p

from kubernetes.config import ComponentConfig

component_config = ComponentConfig.model_validate(p.Config().get_object('config'))
