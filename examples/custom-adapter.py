"""Illustrative adapter shape; the MVP runner supplies the real RunContext."""

class MyAdapter:
    name = "my-adapter"
    version = "0.1"

    def run(self, context):
        observation = context.tools.read("read_state", "example-001")
        if observation.state_version != context.tools.current_version("example-001"):
            return context.needs_review("state changed before commit")
        context.tools.act("commit_effect", "example-001", {}, operation_id="demo-001")
        return context.claim("task_committed", text="verified by adapter")
