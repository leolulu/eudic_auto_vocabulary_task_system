import argparse
import copy
import os
import re
from argparse import Namespace
from pathlib import Path
from time import sleep

from .api.dida365 import Dida365
from .exceptions.backlink_exceptions import TaskNotFoundException
from .models.backlink import BackLink
from .models.link import Link
from .models.target_date import TargetDate
from .models.task import Task
from .models.upload_attachment import uploadAttachment
from .utils.backlink_util import BackLinkUtil
from .utils.common_util import groupby_func
from .utils.decorator_util import ensure_run_retry
from .utils.dict_util import BaiduFanyi
from .utils.dictvoice_util import get_dictvoice_bytes
from .utils.task_selector import TaskSelector
from .utils.time_util import get_days_offset, get_today_arrow


class DidaManipulator:
    PROJECT_WORDS = b"\xe8\x83\x8c\xe5\x8d\x95\xe8\xaf\x8d"

    def __init__(self, args: Namespace) -> None:
        self.args = args
        self.quantity_limit = args.quantity_limit
        self.dida = Dida365()
        self.today_arrow = get_today_arrow()

    def build_backlink(self):
        for task in [i for i in self.dida.active_tasks if i.project_id == "670946db840bf3f353ab7738"]:
            normal_links = Link.dedup_link_with_wls(task._backlink_util.parse_normal_links())
            for normal_link in normal_links:
                target_tasks = [i for i in self.dida.active_tasks if i.id == normal_link.link_task_id]
                if len(target_tasks) == 0:
                    raise TaskNotFoundException()
                target_task = target_tasks[0]
                if len(target_tasks) > 1:
                    raise UserWarning(f"Task id duplicates, id: {normal_link.link_task_id}")
                target_task_backlinks = target_task.backlinks
                if len(target_task_backlinks) == 0:
                    if_add_section = True
                else:
                    if_add_section = False
                task_link = Link.create_link_from_task(task)
                backlink = BackLink(task_link)
                if backlink not in target_task_backlinks:
                    backlink.add_whole_line_str(normal_link.whole_line_str)
                    target_task_backlinks.append(backlink)
                else:
                    backlink = target_task_backlinks[target_task_backlinks.index(backlink)]
                    backlink.add_whole_line_str(normal_link.whole_line_str)
                backlink_section_str = BackLinkUtil.gen_backlink_section(target_task_backlinks)
                if if_add_section:
                    content = "" if target_task.content is None else target_task.content
                    content += "\n\n"
                    content += backlink_section_str
                else:
                    content = "" if target_task.content is None else target_task.content
                    content = re.sub(BackLinkUtil.SECTION_PATTERN, backlink_section_str, content)
                if target_task.content != content:
                    target_task.update_content(content)
                    self.dida.post_task(Task.gen_update_data_payload(target_task.task_dict))
                    print(f'{"Create" if if_add_section else "Update"} backlink: [{target_task.title}] <- [{task.title}]')

    def reset_all_backlinks(self):
        """Use with caution!!!"""
        for task in [i for i in self.dida.active_tasks if i.project_id == "670946db840bf3f353ab7738"]:
            if re.search(BackLinkUtil.SECTION_PATTERN, task.content):
                content = re.sub(BackLinkUtil.SECTION_PATTERN, "", task.content)
                task.update_content(content)
                self.dida.post_task(Task.gen_update_data_payload(task.task_dict))
                print(f"Reset backlink in task: {task.title}")



    def _add_new_ebbinghaus_tasks(self, words):
        template_task = self.find_task("模板版本二")
        for word in words:
            word = word.lower()
            new_task_dict = copy.deepcopy(template_task.task_dict)
            new_task_dict[Task.ID] = new_task_dict[Task.ID] + "z"
            title = word + "📌"
            new_task_dict[Task.TITLE] = title
            bf = BaiduFanyi(word)
            if bf.if_definitions_found:
                new_task_dict[Task.CONTENT] = bf.phonetic_string + "\n" + bf.definitions + "\n"
            print(f"\nAdd ebbinghaus task: {title}")
            self.dida.post_task(Task.gen_add_data_payload(new_task_dict))
            # Upload attachment
            task = self.find_task(title, if_reload_data=True)
            task.add_upload_attachment_post_payload_by_bytes(*get_dictvoice_bytes(word))
            dm.dida.upload_attachment(*task.attachments_to_upload)
            print(f"Add dictvoice for task: {title}")
            # put dictvoice ahead
            self.rearrange_content_put_dictvoice_ahead(title)
        if hasattr(BaiduFanyi, "EDGE_BROWSER"):
            BaiduFanyi.EDGE_BROWSER.close()


    def add_dictvoice_existing_task(self):
        task_title = self.args.add_dictvoice.strip()
        query_word = task_title
        if "~" in task_title:
            task_title, query_word = task_title.split("~")
        task = self.find_task(task_title)
        task.add_upload_attachment_post_payload_by_bytes(*get_dictvoice_bytes(query_word))
        self.dida.upload_attachment(*task.attachments_to_upload)
        print("Dictvoice added.")

    def renew_overdue_task(self):
        overdue_tasks: list[Task] = []
        for i in range(3):
            i = -(i+1)
            overdue_tasks.extend(self._get_target_words_task(i))
        for task in overdue_tasks:
            print(f"Renew task[{task.title}], start date: {task.start_date}")
            task.change_start_date_to_today()
            self.dida.post_task(Task.gen_update_date_payload(task.task_dict))

