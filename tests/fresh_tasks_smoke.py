"""Offline task/evaluator separation and positive/negative control checks."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
try:
    import fresh_tasks
except ImportError:
    fresh_tasks = None


class FreshTasksTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(fresh_tasks, "The approved offline benchmark preparator is not implemented")
        return fresh_tasks

    def test_six_fresh_tasks_and_three_explicit_arms(self):
        module = self.module()
        self.assertEqual(len(module.TASKS), 6)
        self.assertEqual(module.ARMS, ("native", "rules", "rules_jev"))
        self.assertEqual(len({task["id"] for task in module.TASKS}), 6)

    def test_export_contains_no_evaluators_or_reference_solutions(self):
        module = self.module()
        with tempfile.TemporaryDirectory(prefix="jevto-fresh-export-") as temporary:
            destination = Path(temporary) / "campaign"
            manifest = module.prepare(destination, seed=17)
            self.assertFalse(manifest["isolation_verified"])
            self.assertFalse(manifest["paid_runs_authorized"])
            self.assertEqual(len(manifest["schedule"]), 18)
            for task in module.TASKS:
                workspace = destination / "workspaces" / task["id"]
                self.assertEqual({p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()}, set(task["files"]) | {"TASK.md", "test_visible.py"})
            self.assertEqual(manifest, json.loads((destination / "manifest.json").read_text()))
            self.assertEqual(module.verify(destination), [])
            path = destination / "workspaces" / module.TASKS[0]["id"] / "TASK.md"
            path.write_text(path.read_text() + "changed")
            self.assertTrue(module.verify(destination), "Frozen input tampering must be detected")

    def test_refuses_to_overwrite_an_existing_campaign(self):
        module = self.module()
        with tempfile.TemporaryDirectory(prefix="jevto-fresh-existing-") as directory:
            with self.assertRaises(FileExistsError):
                module.prepare(Path(directory), seed=17)

    def test_freeze_rejects_unlisted_workspace_files(self):
        module = self.module()
        with tempfile.TemporaryDirectory(prefix="jevto-fresh-injected-") as temporary:
            destination = Path(temporary) / "campaign"
            module.prepare(destination)
            name = f"workspaces/{module.TASKS[0]['id']}/test_hidden.py"
            module.write(destination / name, "# Injected evaluator must invalidate a frozen export.\n")
            self.assertIn(name, module.verify(destination))

    def test_rust_build_ignores_configured_shared_target_directory(self):
        module = self.module()
        task = next(task for task in module.TASKS if task["language"] == "rust")
        with tempfile.TemporaryDirectory(prefix="jevto-fresh-target-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            for name, text in task["reference"].items():
                module.write(workspace / name, text)
            module.write(workspace / "test_visible.py", task["visible"])
            evaluator = root / "evaluator" / "test_hidden.py"
            module.write(evaluator, task["hidden"])
            shared_target = root / "shared-target"
            module.write(workspace / ".cargo" / "config.toml", "[build]\ntarget-dir = " + json.dumps(shared_target.as_posix()) + "\n")
            visible, hidden, detail = module.run_checks(task, workspace, evaluator)
            self.assertFalse(shared_target.exists(), "Fixture builds must stay in their workspace")
            self.assertTrue(visible and hidden, detail)

    def test_positive_and_negative_controls_for_every_hidden_evaluator(self):
        results = self.module().self_test()
        self.assertEqual(len(results), 6)
        for row in results:
            with self.subTest(task=row["task"]):
                self.assertTrue(row["reference_visible_pass"], row)
                self.assertTrue(row["reference_hidden_pass"], row)
                self.assertTrue(all(row["faulty_visible_passes"]), row)
                self.assertTrue(all(row["faulty_hidden_rejected"]), row)


if __name__ == "__main__":
    unittest.main()
