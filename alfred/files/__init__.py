"""Milestone 1's file tasks: a seeded course folder, an environment that reads it through the permission broker,
four requests with checks, scripted agents, and a runner (python -m alfred.files.run)."""
from .course import Course, build
from .env import FilesEnvironment, system_prompt
from .scripted import CarefulAgent, HostileAgent
from .tasks import TASKS, TASKS_BY_ID, FileTask, mentions, score

__all__ = ["CarefulAgent", "Course", "FileTask", "FilesEnvironment", "HostileAgent", "TASKS", "TASKS_BY_ID", "build",
           "mentions", "score", "system_prompt"]
