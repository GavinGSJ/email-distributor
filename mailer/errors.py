class MailerError(Exception):
    """Base class for every error this package raises on purpose."""


class ConfigError(MailerError):
    pass


class RecipientError(MailerError):
    pass


class MissingVariableError(MailerError):
    def __init__(self, names):
        self.names = sorted(names)
        super().__init__("模板里用到了未提供的变量: " + ", ".join(self.names))


class AttachmentError(MailerError):
    pass


class SignatureError(MailerError):
    pass


class SendError(MailerError):
    pass


class PrepareError(MailerError):
    """Raised when one or more mails cannot be built; nothing has been sent."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))
