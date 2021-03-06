import asyncio
import logging
import pickle
from copy import deepcopy
from dataclasses import dataclass
from typing import Tuple, Dict

import asyncpg

from .notifications import NotificationType


@dataclass
class SubscriptionOptions:
    """A dataclass for holding channel subscription options."""

    notification_type: NotificationType
    launch_mentions: str


class DataStore:
    """A class to handle storing bot data.

    Must supply arguments for asyncpg.create_pool such as user, host, port, database.

    Stateful data is stored by serializing and loading from a file, subscribed channels
    data is stored in a postgres database.

    All methods that either return or take mutable objects as parameters make a deep
        copy of said object(s) so that changes cannot be made outside the instance.

    Immutable object references:
     - https://stackoverflow.com/a/23715872/6396652
     - https://stackoverflow.com/a/986145/6396652
    """

    def __init__(
        self, loop: asyncio.AbstractEventLoop, pickle_file_path: str, **kwargs
    ):
        self._pickle_file_path = pickle_file_path
        logging.info("Creating a connection pool for DB")
        self.db_pool = loop.run_until_complete(asyncpg.create_pool(**kwargs))
        logging.info("Pooled")

        # Boolean indicating if a launch notification has been sent for the current
        # schedule.
        self._launch_embed_for_current_schedule_sent: bool = False
        # A dict of the most previously sent schedule embed (for comparison).
        self._previous_schedule_embed_dict: Dict = {}

        try:
            with open(self._pickle_file_path, "rb") as f_in:
                tmp = pickle.load(f_in)
            self.__dict__.update(tmp)
            logging.info(f"Updated self.__dict__ from {self._pickle_file_path}")
        except FileNotFoundError:
            logging.info(f"Could not find file at location: {self._pickle_file_path}")

    def save_state(self) -> None:
        # Idea from https://stackoverflow.com/a/2842727/6396652.
        # pylint: disable=line-too-long
        to_dump = {
            "_launch_embed_for_current_schedule_sent": self._launch_embed_for_current_schedule_sent,
            "_previous_schedule_embed_dict": self._previous_schedule_embed_dict,
        }
        with open(self._pickle_file_path, "wb") as f_out:
            pickle.dump(to_dump, f_out, protocol=pickle.HIGHEST_PROTOCOL)

    def get_notification_task_vars(self) -> Tuple[bool, Dict]:
        return (
            self._launch_embed_for_current_schedule_sent,
            deepcopy(self._previous_schedule_embed_dict),
        )

    def set_notification_task_vars(
        self,
        launch_embed_for_current_schedule_sent: bool,
        previous_schedule_embed_dict: Dict,
    ) -> None:
        self._launch_embed_for_current_schedule_sent = (
            launch_embed_for_current_schedule_sent
        )
        self._previous_schedule_embed_dict = deepcopy(previous_schedule_embed_dict)
        self.save_state()

    async def add_subbed_channel(
        self,
        channel_id: str,
        channel_name: str,
        guild_id: str,
        notif_type: NotificationType,
        launch_mentions: str,
    ) -> bool:
        """Add a channel to subscribed channels.

        Args:
            channel_id: The channel to add.
            channel_name: The name of the channel.
            guild_id: The guild the channel is in.
            notif_type: The type of subscription.
            launch_mentions: The mentions for launch notifications.

        Returns:
            A bool indicating if the channel was added or not.

        """
        notification_type = notif_type.name
        sql = """
        insert into subscribed_channels
            (channel_id, guild_id, channel_name, notification_type, launch_mentions)
        values
            ($1, $2, $3, $4, $5);"""
        async with self.db_pool.acquire() as conn:
            try:
                response = await conn.execute(
                    sql,
                    channel_id,
                    guild_id,
                    channel_name,
                    notification_type,
                    launch_mentions,
                )
                # Not great practice but it works.
                if response == "INSERT 0 1":
                    return True
            except asyncpg.exceptions.UniqueViolationError:
                # channel_id (primary key) already exists.
                pass
        return False

    async def get_subbed_channels(self) -> Dict[int, SubscriptionOptions]:
        channels = {}
        sql = "select * from subscribed_channels;"
        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(sql)
        for rec in records:
            cid = int(rec["channel_id"])
            notif_type = rec["notification_type"]
            mentions = (
                rec["launch_mentions"] if rec["launch_mentions"] is not None else ""
            )
            channels[cid] = SubscriptionOptions(NotificationType[notif_type], mentions)
        return channels

    async def remove_subbed_channel(self, channel_id: str) -> bool:
        sql = "delete from subscribed_channels where channel_id = $1;"
        async with self.db_pool.acquire() as conn:
            response = await conn.execute(sql, channel_id)
            if response == "DELETE 1":
                return True
        return False

    async def subbed_channels_count(self) -> int:
        sql = "select count(*) from subscribed_channels;"
        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(sql)
        return int(records[0]["count"])
