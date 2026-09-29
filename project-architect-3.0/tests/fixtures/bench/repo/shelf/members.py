"""Member registry. Ids are 'M' plus four digits, issued in order."""
from . import policy
from .models import Member, NotFound


class MemberRegistry:
    def __init__(self):
        self._members = {}
        self._next = 1

    def register(self, name, email="", kind="standard"):
        policy.check_kind(kind)
        member_id = f"M{self._next:04d}"
        self._next += 1
        member = Member(member_id, name, email, kind)
        self._members[member_id] = member
        return member

    def add(self, member):
        """Insert an existing Member (storage uses this); later ids stay unique."""
        self._members[member.member_id] = member
        self._next = max(self._next, int(member.member_id[1:]) + 1)
        return member

    def get(self, member_id):
        member = self._members.get(member_id)
        if member is None:
            raise NotFound(f"no member {member_id}")
        return member

    def find_by_email(self, email):
        """The member with this email (case-insensitive), or None."""
        want = email.strip().lower()
        for member in self._members.values():
            if member.email.lower() == want:
                return member
        return None

    def deactivate(self, member_id):
        self.get(member_id).active = False

    def active(self):
        return [m for m in self if m.active]

    def __iter__(self):
        return iter(sorted(self._members.values(), key=lambda m: m.member_id))

    def __len__(self):
        return len(self._members)
