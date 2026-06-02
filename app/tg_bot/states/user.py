from aiogram.fsm.state import State, StatesGroup


class UserVerificationStates(StatesGroup):
    waiting_for_verification = State()


__all__ = ("UserVerificationStates",)
