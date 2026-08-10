class Attachment:
    FILE_STRING_TEMPLATE = "![file]({})"
    IMAGE_STRING_TEMPLATE = "![image]({})"
    ID = "id"
    REF_ID = "refId"
    PATH = "path"
    SIZE = "size"
    FILE_NAME = "fileName"
    FILE_TYPE = "fileType"
    STATUS = "status"
    CREATED_TIME = "createdTime"

    def __init__(self, attachment_dict) -> None:
        self.attachment_dict = attachment_dict
        self._load_field()
        self._post_process()

    def _post_process(self):
        if self.status and self.status == 1:
            self.is_active = False
        else:
            self.is_active = True
        self.content_file_path = "{}/{}".format(self.id, self.file_name)
        template = (
            Attachment.IMAGE_STRING_TEMPLATE
            if (self.file_type or "").upper() == "IMAGE"
            else Attachment.FILE_STRING_TEMPLATE
        )
        self.content_file_string = template.format(self.content_file_path)

    def _load_field(self):
        self.id = self.attachment_dict.get(Attachment.ID)
        self.ref_id = self.attachment_dict.get(Attachment.REF_ID)
        self.path = self.attachment_dict.get(Attachment.PATH)
        self.size = self.attachment_dict.get(Attachment.SIZE)
        self.file_name = self.attachment_dict.get(Attachment.FILE_NAME)
        self.file_type = self.attachment_dict.get(Attachment.FILE_TYPE)
        self.status = self.attachment_dict.get(Attachment.STATUS)
        self.created_time = self.attachment_dict.get(Attachment.CREATED_TIME)
