"""Entry point. Edit ProjectContext below for your task."""

import anyio
from context import ProjectContext
from pipeline import run_pipeline

ctx = ProjectContext.from_repo(
    repo_path="/home/vkhare/cloudn",
    jira_ticket="AVX-73788",
    task="Overlay prefix should Advertise Transit VPC CIDR only when \"Advertise Transit VPC/VNet CIDR\" is enabled",
    branch="master",
    language="Go",
    test_commands=[
        "bazel test //go/aviatrix.com/conduit/v2/gateway-conduit:gateway-conduit_test",
        "bazel test //go/aviatrix.com/launcher/launchertest:launchertest_etcd_test",
        "bazel test //go/aviatrix.com/launcher/gateway_launcher:gateway_launcher_test",
        "bazel test //go/aviatrix.com/conduit/v2/controller-conduit:controller-conduit_test",
    ],
    lint_commands=[
        "make lint-standard",
        "make lint-avx",
        "make lint-strict",
    ],
)

anyio.run(run_pipeline, ctx)
