from collections import defaultdict
from enum import Enum
from functools import partial

import zope.interface as zi


class Layer(Enum):
    architecture = 1
    item = 2
    creature = 3


class ThingType:
    def __init__(self, *components, layer, name, tmp_rendering, modifiers=()):
        self.layer = layer
        self.name = name
        self.tmp_rendering = tmp_rendering
        self.modifiers = modifiers

        self.components = {}
        for component in components:
            for iface in zi.implementedBy(component):
                if iface is IComponent:
                    continue
                if iface in self.components:
                    raise TypeError(
                        "Got two components for the same interface "
                        "({!r}): {!r} and {!r}"
                        .format(iface, self.components[iface], component))
                self.components[iface] = component

    def __call__(self, *args, **kwargs):
        return Thing(self, *args, **kwargs)


class Thing:
    def __init__(self, type):
        self.type = type
        self.modifiers = []
        self.component_data = {}

    def __repr__(self):
        return "<{}: {}>".format(
            type(self).__qualname__,
            self.type.name,
        )

    def __conform__(self, iface):
        # z.i method called on an object to ask it to adapt itself to some
        # interface
        # TODO handle keyerror?  or don't?
        component = self.type.components[iface]
        return component(iface, self)

    def add_modifiers(self, *modifiers):
        # Temporarily inject another source's modifiers onto this thing.
        # TODO: these should know their source and why: (Armor, equipment)
        # TODO: i would prefer if these disappeared on their own, somehow,
        # rather than relying on an event.  but probably the event should be
        # reliable anyway.
        self.modifiers.extend(modifiers)
        # TODO: fire events when stats change?  (is that how the UI should be
        # updated?)

    def isa(self, thing_type):
        # TODO unclear how this will handle inherited properties, or if it ever
        # needs to (well, surely we want e.g. Potion)
        return self.type is thing_type

    @property
    def layer(self):
        return self.type.layer

    def handle_event(self, event):
        for iface, component in self.type.components.items():
            adapted = component(iface, self)
            adapted.handle_event(adapted, event)




class Handler:
    @classmethod
    def wrap(cls, func, event_class):
        if isinstance(func, Handler):
            func.add(event_class)
            return func
        else:
            return cls(func, event_class)

    def __init__(self, func, event_class):
        self.func = func
        self.event_classes = [event_class]

    def add(self, event_class):
        self.event_classes.append(event_class)

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)


# TODO: i feel like instead of having two of every event, i'd kind of like to
# have events fire in two passes: during the first, any handler can cancel the
# event or succeed the event, either of which stops further processing; during
# the second, any handler can respond to the success of the event.
# so the base Equipment can have an Equip handler that just equips it and adds
# modifiers; if you want to make armor that sometimes can't be equipped, you
# add a regular first-pass handler that can cancel, but if you want armor that
# does something extra /after/ it's equipped successfully, you do second-pass.
# and then if nothing calls .succeed(), the event is assumed to have failed,
# which would also help prevent a few kinds of mistakes i've already made oops.
# but then, that might only work if both the actor and the target get to
# respond?  i.e. you fire Drink at a potion, but if you have some armor that
# does something with potions, its event handlers are attached to /you/ rather
# than to all potions everywhere.  maybe that's just part of the idea of having
# event handlers from different 'directions' though??
def handler(event_class):
    def decorator(f):
        return Handler.wrap(f, event_class)

    return decorator

class IComponent(zi.Interface):
    pass


class ComponentMeta(type):
    def __new__(meta, name, bases, attrs):
        event_handlers = defaultdict(list)

        for key, value in list(attrs.items()):
            if isinstance(value, Handler):
                for cls in value.event_classes:
                    event_handlers[cls].append(value.func)

                del attrs[key]

        # TODO should this automatically include bases' handlers?
        attrs['event_handlers'] = event_handlers

        return super().__new__(meta, name, bases, attrs)


class ComponentAttribute:
    def __init__(desc, zope_attribute, initializer):
        desc.zope_attribute = zope_attribute
        desc.initializer = initializer

    def __get__(desc, self, cls):
        if self is None:
            return desc

        # TODO how does this get set initially, though...
        attr = desc.zope_attribute
        data = self.entity.component_data

        if attr not in data:
            data[attr] = desc.initializer(self)

        value = data[attr]

        for mod in self.entity.modifiers:
            value = mod.modify(attr, value)

        return value

    def __set__(desc, self, value):
        self.entity.component_data[desc.zope_attribute] = value


def attribute(iface):
    def decorator(f):
        return ComponentAttribute(iface[f.__name__], f)
    return decorator


@zi.implementer(IComponent)
class Component(metaclass=ComponentMeta):
    def __init__(self, iface, entity):
        self.iface = iface
        self.entity = entity

    def handle_event(self, thing, event):
        # TODO seems a bit odd that we're receiving the actual Thing here
        # TODO what order should these be called in?
        for event_class in type(event).__mro__:
            for handler in self.event_handlers[event_class]:
                # TODO at this point we are nested three loops deep
                handler(thing, event)



class IPhysics(IComponent):
    def blocks(actor):
        """Return True iff this object won't allow `actor` to move on top of
        it.
        """


from flax.event import Walk

@zi.implementer(IPhysics)
class Solid(Component):
    def blocks(self, actor):
        # TODO i have /zero/ idea how passwall works here
        return True

    # TODO there's a fuzzy line here.  what's the difference between a
    # component method and an event handler?  shouldn't *any* IPhysics object
    # respond to Walk?  isn't that the whole point of a physical object?
    # obviously there should be support for exceptions, but i feel like
    # requiring a component implementation to respond to default events (and
    # perhaps even associating each event with a specific interface somehow)
    # would make this all make a bit more...  predictable.  and i think that
    # would make the semantics a little better: most events are, in a way,
    # really just calls to component methods that other things can twiddle
    # TODO also seems like i should /require/ that every ThingType has a
    # IPhysics, maybe others...
    @handler(Walk)
    def handle_walk(self, event):
        event.cancel()


class IContainer(IComponent):
    inventory = zi.Attribute("""Items contained by this container.""")


from flax.event import PickUp

@zi.implementer(IContainer)
class Container(Component):
    @attribute(IContainer)
    def inventory(self):
        return []


@zi.implementer(IPhysics)
class Empty(Component):
    def blocks(self, actor):
        return False

    @handler(Walk)
    def handle_walk(self, event):
        event.world.current_map.move(event.actor, event.target.position)


class ICombatant(IComponent):
    """Implements an entity's ability to fight and take damage."""
    health = zi.Attribute("""Entity's health meter.""")

    strength = zi.Attribute("""Generic placeholder stat while I figure stuff out.""")

    def damage(amount):
        """Take damage.

        Don't override this to respond to damage; handle the Damage event
        instead.
        """


from flax.event import MeleeAttack, Damage, Die


@zi.implementer(ICombatant)
class Combatant(Component):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @attribute(ICombatant)
    def health(self):
        return 10

    @attribute(ICombatant)
    def strength(self):
        return 3

    @handler(Damage)
    def handle_damage(self, event):
        self.health -= event.amount

        if self.health <= 0:
            event.world.queue_immediate_event(Die(self.entity))

    @handler(MeleeAttack)
    def handle_attack(self, event):
        print("{0} hits {1}".format(event.actor.type.name, self.entity.type.name))

        # TODO what's the amount
        event.world.queue_immediate_event(Damage(self.entity, 5))

    @handler(Die)
    def handle_death(self, event):
        # TODO player death is different; probably raise an exception for the
        # ui to handle?
        print("{} has died".format(self.entity.type.name))
        event.world.current_map.remove(self.entity)
        # TODO and drop inventory, and/or a corpse





class IActor(IComponent):
    """Implements an entity's active thought process.  An entity with an
    `IActor` component can decide to perform actions on its own, and has a
    sense of speed and time.
    """
    def act(world):
        """Return an action to be performed (i.e., an `Event` to be fired), or
        `None` to do nothing.
        it.
        """


@zi.implementer(IActor)
class GenericAI(Component):
    def act(self, world):
        from flax.geometry import Direction
        from flax.event import Walk
        from flax.event import MeleeAttack
        import random
        pos = world.current_map.find(self.entity).position
        player_pos = world.current_map.find(world.player).position
        for direction in Direction:
            if pos + direction == player_pos:
                world.queue_event(MeleeAttack(self.entity, direction))
                return

        # TODO try to walk towards player
        world.queue_event(Walk(self.entity, random.choice(list(Direction))))


@zi.implementer(IActor)
class PlayerIntelligence(Component):
    def act(self, world):
        pass


Architecture = partial(ThingType, layer=Layer.architecture)

CaveWall = Architecture(
    Solid,
    name='wall',
    tmp_rendering=(' ', 'default'))
Wall = Architecture(
    Solid,
    name='wall',
    tmp_rendering=('▒', 'default'))
Floor = Architecture(
    Empty,
    name='dirt',
    tmp_rendering=('·', 'floor'))
Tree = Architecture(
    Solid,
    name='tree',
    tmp_rendering=('↟', 'grass'))
Grass = Architecture(
    Empty,
    name='grass',
    tmp_rendering=('ʬ', 'grass'))
CutGrass = Architecture(
    Empty,
    name='freshly-cut grass',
    tmp_rendering=('░', 'grass'))
Dirt = Architecture(
    Empty,
    name='dirt',
    tmp_rendering=('░', 'dirt'))


Creature = partial(ThingType, Solid, Combatant, Container, layer=Layer.creature)

Player = Creature(PlayerIntelligence, name='you', tmp_rendering=('☻', 'player'))

Salamango = Creature(GenericAI, name='salamango', tmp_rendering=(':', 'salamango'))


class IPortable(IComponent):
    """Entity can be picked up and placed in containers."""


@zi.implementer(IPortable)
class Portable(Component):
    # TODO maybe "actor" could just be an event target, and we'd need fewer
    # duplicate events for the source vs the target?
    @handler(PickUp)
    def handle_picked_up(self, event):
        print("ooh picking up", self.entity.type.name)
        assert self.entity.type.layer is Layer.item
        event.world.current_map.remove(self.entity)
        IContainer(event.actor).inventory.append(self.entity)


Item = partial(ThingType, Portable, layer=Layer.item)

class IUsable(IComponent):
    def use():
        pass


@zi.implementer(IUsable)
class UsablePotion(Component):
    def use(self):
        return effect.Heal()

#potion = Item(UsablePotion, name="potion")
Potion = Item(UsablePotion, name='potion', tmp_rendering=('ð', 'default'))



class IEquipment(IComponent):
    pass

from flax.event import Equip

@zi.implementer(IEquipment)
class Equipment(Component):
    # TODO turn this into a general "while equipped"?  i guess that's really
    # just sugar
    @handler(Equip)
    def handle_equip(self, event):
        # TODO lol it's possible to put something on twice whoops.  this needs
        # to actually do some kind of equipment association anyway.
        print("you put on the armor")
        # Careful to use the /type/'s modifiers here, not the item's!
        # TODO consider renaming either attribute
        event.actor.add_modifiers(*self.entity.type.modifiers)

    # TODO this part is probably important now, not that there's a key for
    # uneqipping yet either
    #@handler(Unequip)
    #def handle_unequip(self, event):
    #    pass

    #@handler(Damage, on=wearer)
    #def handle_wearer_damage(self, event):
    pass


class Modifier:
    def __init__(self, stat, add):
        self.stat = stat
        self.add = add

    def modify(self, attr, value):
        if attr is not self.stat:
            return value

        return value + self.add

Armor = Item(Equipment, name='armor', tmp_rendering=('[', 'default'), modifiers=[Modifier(ICombatant['strength'], add=3)])


# TODO
# - figure out the role of a component.  if we're mostly doing message/event
# passing, what code does a component actually need to have?
# - how do values work?  how do modifiers and other effects work?
# - implement...
#       armor that reduces damage by half
#       forestwalk
#       basic stats
