"""Which channel a prospect is written to on, and which lane they belong to.

A client with no lanes has one channel for everyone: `ChannelRouter.single(channel)` is exactly the
Engine's old behaviour, and it never attributes anything. A client that runs lanes hands the Engine
a router holding one adapter per channel in use and the lanes (each lane names its channel), so
several lanes may share a channel and several channels may coexist.

Attribution is deterministic and, once made, final: a prospect already carrying a `lane_id` keeps it
whatever the configuration says now (if the lane no longer exists, they are in no lane, and nothing
is sent to them); one that carries none is attributed by the prefix of their id, else to the default
lane, else to none. Persisting the attribution is the Engine's job, not this class's.
"""

from collections.abc import Mapping, Sequence

from sales_core.contracts import Channel, LaneId, LaneRoute, Prospect
from sales_core.ports import MessagingChannel

_SEPARATOR = ":"


class ChannelRouter:
    def __init__(
        self,
        channels: Mapping[Channel, MessagingChannel],
        lanes: Sequence[LaneRoute] = (),
        *,
        default_lane: LaneId | None = None,
    ) -> None:
        if not channels:
            raise ValueError("a router needs at least one channel")
        for key, adapter in channels.items():
            if adapter.channel != key:
                raise ValueError(f"the adapter filed under {key!r} is a {adapter.channel!r} one")
        if not lanes and len(channels) != 1:
            raise ValueError("a router with no lanes serves exactly one channel")
        by_id: dict[LaneId, LaneRoute] = {}
        for lane in lanes:
            if lane.lane_id in by_id:
                raise ValueError(f"lane {lane.lane_id!r} is listed twice")
            if lane.channel not in channels:
                raise ValueError(
                    f"lane {lane.lane_id!r} uses {lane.channel!r}, which has no adapter"
                )
            by_id[lane.lane_id] = lane
        if default_lane is not None and default_lane not in by_id:
            raise ValueError(f"the default lane {default_lane!r} is not one of the lanes")
        self._channels = dict(channels)
        self._lanes = by_id
        self._default = default_lane

    @classmethod
    def single(cls, channel: MessagingChannel) -> "ChannelRouter":
        """The client that has no lanes: one channel, for everyone."""
        return cls({channel.channel: channel})

    def __repr__(self) -> str:
        return f"ChannelRouter(channels={sorted(self._channels)}, lanes={sorted(self._lanes)})"

    @property
    def explicit(self) -> bool:
        """Whether the client runs lanes. If not, nothing is attributed, pinned or filtered."""
        return bool(self._lanes)

    @property
    def channels(self) -> tuple[Channel, ...]:
        return tuple(self._channels)

    @property
    def any_channel(self) -> Channel:
        return next(iter(self._channels))

    def adapter(self, channel: Channel) -> MessagingChannel | None:
        return self._channels.get(channel)

    def lane(self, lane_id: LaneId | None) -> LaneRoute | None:
        return self._lanes.get(lane_id) if lane_id is not None else None

    def lane_for(self, prospect: Prospect) -> LaneRoute | None:
        """The lane of a prospect, attributed or not yet; None if there are no lanes or the
        prospect is in none that the client runs."""
        if not self.explicit:
            return None
        if prospect.lane_id is not None:
            return self._lanes.get(prospect.lane_id)  # final: never re-attributed
        return self.attribute(prospect)

    def attribute(self, prospect: Prospect) -> LaneRoute | None:
        """The lane an unattributed prospect belongs to: their source's, else the default one."""
        for lane in self._lanes.values():
            if lane.source_id and prospect.prospect_id.startswith(f"{lane.source_id}{_SEPARATOR}"):
                return lane
        return self._lanes.get(self._default) if self._default is not None else None

    def lane_for_inbound(self, channel: Channel) -> LaneRoute | None:
        """The lane of someone who wrote to us first on `channel`: the default lane if it uses
        that channel, else the first lane that does, else the default lane."""
        default = self._lanes.get(self._default) if self._default is not None else None
        if default is not None and default.channel is channel:
            return default
        for lane in self._lanes.values():
            if lane.channel is channel:
                return lane
        return default

    def channel_for(self, prospect: Prospect) -> Channel | None:
        """The channel to write to this prospect on, or None if they are in no lane."""
        if not self.explicit:
            return self.any_channel
        lane = self.lane_for(prospect)
        return lane.channel if lane is not None else None
