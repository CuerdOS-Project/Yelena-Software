import ast
import unittest
from pathlib import Path


TASK_MANAGER_PATH = Path(__file__).resolve().parents[1] / "backend" / "task_manager.py"


class TaskManagerImportLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = ast.parse(TASK_MANAGER_PATH.read_text(encoding="utf-8"))
        cls.worker = next(
            node for node in cls.module.body
            if isinstance(node, ast.ClassDef) and node.name == "TaskWorker"
        )
        cls.run_method = next(
            node for node in cls.worker.body
            if isinstance(node, ast.FunctionDef) and node.name == "run"
        )

    def test_appimage_backend_is_imported_at_module_load(self):
        imports = [
            node for node in self.module.body
            if isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module is None
        ]
        imported_names = {
            alias.name for node in imports for alias in node.names
        }
        self.assertIn("appimage_backend", imported_names)

    def test_worker_run_has_no_dynamic_imports(self):
        imports = [
            node for node in ast.walk(self.run_method)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertEqual(imports, [], "TaskWorker.run must not import modules in QThread")


if __name__ == "__main__":
    unittest.main()
