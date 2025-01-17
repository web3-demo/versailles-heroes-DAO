import brownie
from tests.conftest import approx
from random import random, randrange

import pytest

H = 3600
DAY = 86400
WEEK = 7 * DAY
MONTH = 4 * WEEK
YEAR = 365 * 86400
MAXTIME = 126144000
TOL = 120 / WEEK


@pytest.fixture(scope="module", autouse=True)
def initial_setup(chain, accounts, token, gas_token, voting_escrow, guild_controller, minter, reward_vesting):
    alice, bob, carl = accounts[:3]
    amount_alice = 110000 * 10 ** 18
    amount_bob = 110000 * 10 ** 18
    amount_carl = 1000000 * 10 ** 18
    token.transfer(bob, amount_alice, {"from": alice})
    token.transfer(bob, amount_bob, {"from": alice})
    token.transfer(carl, amount_carl, {"from": alice})

    chain.sleep(DAY + 1)
    token.update_mining_parameters()

    token.approve(voting_escrow.address, amount_alice * 10, {"from": alice})
    token.approve(voting_escrow.address, amount_bob * 10, {"from": bob})
    token.approve(voting_escrow.address, amount_carl * 10, {"from": carl})

    # Move to timing which is good for testing - beginning of a UTC week
    chain.sleep((chain[-1].timestamp // WEEK + 1) * WEEK - chain[-1].timestamp)
    chain.mine()

    chain.sleep(H)

    voting_escrow.create_lock(amount_alice, chain[-1].timestamp + MAXTIME, {"from": alice})
    voting_escrow.create_lock(amount_bob, chain[-1].timestamp + MAXTIME, {"from": bob})
    voting_escrow.create_lock(amount_carl, chain[-1].timestamp + MAXTIME, {"from": carl})

    chain.sleep(H)
    chain.mine()

    token.set_minter(minter.address, {"from": accounts[0]})
    guild_controller.set_minter(minter.address, {"from": accounts[0]})
    reward_vesting.set_minter(minter.address, {"from": accounts[0]})
    chain.sleep(10)


def test_join_guild_twice(chain, accounts, token, gas_token, voting_escrow, guild_controller, minter, reward_vesting,
                          Guild):
    """
    Test join guild
    """
    alice = accounts[0]
    bob = accounts[1]
    guild = create_guild(chain, guild_controller, gas_token, alice, 0, Guild)
    chain.sleep(60)
    chain.mine()
    # join guild for the first time
    guild.join_guild({"from": bob})
    chain.sleep(60)
    chain.mine()
    # join guild for the second time revert
    with brownie.reverts("Already in a guild"):
        guild.join_guild({"from": bob})


def test_leave_guild(chain, accounts, token, gas_token, voting_escrow, guild_controller, minter, reward_vesting, Guild):
    '''
    test leave guild voting power decrease, integrate fraction not change and only can claim reward vesting
    :return:
    '''
    alice = accounts[0]
    bob = accounts[1]
    guild = create_guild(chain, guild_controller, gas_token, alice, 0, Guild)
    alice_voting_power = voting_escrow.balanceOf(alice)
    alice_slope = voting_escrow.get_last_user_slope(alice)
    chain.sleep(WEEK)
    chain.mine()
    # join guild
    guild.join_guild({"from": bob})
    chain.sleep(2 * WEEK + 1)
    chain.mine()
    # bob leave guild
    tx = guild.leave_guild({"from": bob})
    print(tx.events)
    # record integrate fraction
    integrate_fraction_after_leave_guild = guild.integrate_fraction(bob)
    chain.sleep(60)
    chain.mine()
    # check guild voting power decrease by bob's voting power
    next_time_voting_power = guild_controller.get_guild_weight(guild.address)
    # bob leave, left only alice, get alice voting power
    current_alice_power = alice_voting_power - alice_slope * (
        ((chain[-1].timestamp // WEEK + 1) * WEEK - chain[-1].timestamp + 3 * WEEK))

    assert approx(current_alice_power, next_time_voting_power, TOL)

    # advance to reward vesting epoch start
    chain.sleep((chain[-1].timestamp // MONTH + 1) * MONTH - chain[-1].timestamp)
    chain.mine()
    chain.sleep(WEEK)
    chain.mine()
    tx = minter.mint({"from": bob})
    print(tx.events)
    # integration fraction
    expect_integrate_fraction = guild.integrate_fraction(bob)

    guild.join_guild({"from": bob})
    assert integrate_fraction_after_leave_guild == expect_integrate_fraction, "should not increase integrate fraction between leave guild and join guild"
    
    for _ in range(8): # forward 4 years
        chain.sleep(26 * WEEK) # forward 6 months
        chain.mine()
        guild.user_checkpoint(bob, {"from": bob}) # perform user_checkpoint to avoid out of gas
    
    assert voting_escrow.balanceOf(bob) == 0, "balance should be 0 when vote lock ends"

    tx = guild.leave_guild({"from": bob}) # should not error if balance is 0 when leaving guild

    assert guild_controller.global_member_list(bob) == brownie.ZERO_ADDRESS, "user should no longer in guild"
    assert guild.integrate_fraction(bob) == minter.minted(bob, guild.address), "user's claimable tokens should already minted completely"


def test_increase_amount_guild_weight_increase(chain, accounts, token, gas_token, voting_escrow, guild_controller,
                                               minter, reward_vesting, Guild):
    '''
    test_increase_amount_guild_weight_increase
    :return:
    '''
    alice = accounts[0]
    guild = create_guild(chain, guild_controller, gas_token, alice, 0, Guild)

    chain.sleep(WEEK)
    chain.mine()
    increase_amount = 20000 * 10 ** 18
    voting_escrow.increase_amount(increase_amount, {"from": alice})
    alice_power_after_increase = voting_escrow.balanceOf(alice)
    alice_slope = voting_escrow.get_last_user_slope(alice)
    guild.user_checkpoint(alice, {"from": alice})
    chain.sleep(10)
    chain.mine()
    # update working balance once more for check
    guild.user_checkpoint(alice, {"from": alice})
    alice_power_at_next_time = alice_power_after_increase - alice_slope * (
            (chain[-1].timestamp // WEEK + 1) * WEEK - chain[-1].timestamp)
    guild_voting_weight = guild_controller.get_guild_weight(guild.address)
    assert approx(alice_power_at_next_time, guild_voting_weight, TOL)


def test_guild_integral_without_boosting(chain, accounts, token, gas_token, voting_escrow, guild_controller,
                                         minter, reward_vesting, Guild):
    alice, bob = accounts[:2]
    integral = 0  # ∫(balance * rate(t) / totalSupply(t) dt)
    checkpoint_rate = token.rate()
    guild = create_guild(chain, guild_controller, gas_token, alice, 0, Guild)  # create with 0 commission rate
    chain.sleep((chain[-1].timestamp // WEEK + 1) * WEEK - chain[-1].timestamp)
    chain.mine()
    checkpoint = chain[-1].timestamp
    checkpoint_supply = 0
    checkpoint_balance = 0

    def update_integral(is_update_working_balance):
        nonlocal checkpoint, checkpoint_rate, integral, checkpoint_balance, checkpoint_supply

        t1 = chain[-1].timestamp
        rate1 = token.rate()
        t_epoch = token.start_epoch_time()
        if checkpoint >= t_epoch:
            rate_x_time = (t1 - checkpoint) * rate1
        else:
            rate_x_time = (t_epoch - checkpoint) * checkpoint_rate + (t1 - t_epoch) * rate1
        if checkpoint_supply > 0:
            integral += rate_x_time * checkpoint_balance // checkpoint_supply
        checkpoint_rate = rate1
        checkpoint = t1
        checkpoint_supply = guild.working_supply()
        if is_update_working_balance:
            checkpoint_balance = guild.working_balances(alice)

    chain.sleep(10000)
    chain.mine()
    guild.user_checkpoint(alice, {"from": alice})
    checkpoint_supply = guild.working_supply()
    checkpoint_balance = guild.working_balances(alice)
    update_integral(True)
    # Now let's have a loop where Bob always mint, join, leave
    # and Alice does so more rarely
    dt = randrange(1, WEEK)
    chain.sleep(dt)
    chain.mine()
    guild.join_guild({"from": bob})
    update_integral(False)

    dt = randrange(1, WEEK)
    chain.sleep(dt)
    chain.mine()
    guild.user_checkpoint(bob, {"from": bob})
    update_integral(False)

    guild.user_checkpoint(alice, {"from": alice})
    update_integral(True)
    assert approx(guild.integrate_fraction(alice), integral, TOL)
    for i in range(40):
        is_alice = random() < 0.2
        dt = randrange(1, YEAR // 20)
        chain.sleep(dt)
        chain.mine()

        # for bob mint
        is_mint = (i > 0) * (random() < 0.5)
        if is_mint:
            minter.mint({"from": bob})
            update_integral(False)

        # for alice
        if is_alice and is_mint:
            minter.mint({"from": alice})
            update_integral(True)

        if random() < 0.5:
            guild.user_checkpoint(alice, {"from": alice})
            update_integral(True)
        if random() < 0.5:
            guild.user_checkpoint(bob, {"from": bob})
            update_integral(False)

        dt = randrange(1, YEAR // 20)
        chain.sleep(dt)
        chain.mine()

        guild.user_checkpoint(alice, {"from": alice})
        update_integral(True)
        print(i, dt / 86400, integral, guild.integrate_fraction(alice))
        assert approx(guild.integrate_fraction(alice), integral, TOL)


def test_boosting(chain, accounts, token, gas_token, voting_escrow, guild_controller,
                  minter, reward_vesting, Guild, GasEscrow):
    alice = accounts[0]
    bob = accounts[1]
    carl = accounts[2]
    # create guild with commission rate = 0, easy for test
    guild = create_guild(chain, guild_controller, gas_token, alice, 0, Guild)

    # skip boost warm up
    chain.sleep(2 * WEEK + 1)
    chain.mine()
    guild.user_checkpoint(alice, {"from": alice})

    chain.sleep(10)
    chain.mine()
    gas_amount = 100000 * 10 ** 18
    # alice get boost
    setup_gas_token(accounts, alice, gas_amount, gas_token, guild_controller, GasEscrow)
    guild.user_checkpoint(alice, {"from": alice})
    # bob join guild
    guild.join_guild({"from": bob})
    chain.sleep(10)
    chain.mine()
    guild.user_checkpoint(bob, {"from": bob})
    guild.user_checkpoint(alice, {"from": alice})
    # record alice and bob integrate fraction
    alice_integrate_fraction_1st = guild.integrate_fraction(alice)
    bob_integrate_fraction_1st = guild.integrate_fraction(bob)

    dt = randrange(1, 4 * WEEK)
    chain.sleep(dt)
    chain.mine()
    # check integrate fraction of bob and alice
    guild.user_checkpoint(bob, {"from": bob})
    bob_integrate_fraction_2nd = guild.integrate_fraction(bob)
    bob_reward = bob_integrate_fraction_2nd - bob_integrate_fraction_1st

    guild.user_checkpoint(alice, {"from": alice})
    alice_integrate_fraction_2nd = guild.integrate_fraction(alice)
    alice_reward = alice_integrate_fraction_2nd - alice_integrate_fraction_1st
    # alice get 2.5x rewards compare with bob
    assert approx(alice_reward / bob_reward, 2.5, 1e-5)

    # carl join in, this will increase S and make sure alice, bob boost 2.5x
    guild.join_guild({"from": carl})
    guild.user_checkpoint(carl, {"from": carl})

    # check alice gas amount
    gas_escrow = guild_controller.gas_addr_escrow(gas_token.address)
    alice_gas_amount = GasEscrow.at(gas_escrow).balanceOf(alice)
    # add same gas for bob with alice
    setup_gas_token(accounts, bob, alice_gas_amount, gas_token, guild_controller, GasEscrow)
    # record bob integrate fraction
    tx = guild.user_checkpoint(bob, {"from": bob})
    bob_integrate_fraction_2nd = guild.integrate_fraction(bob)

    dt = randrange(1, WEEK)
    chain.sleep(dt)
    chain.mine()
    # get alice integrate fraction
    guild.user_checkpoint(alice, {"from": alice})
    alice_integrate_fraction_3nd = guild.integrate_fraction(alice)
    # get bob integrate fraction
    guild.user_checkpoint(bob, {"from": bob})
    bob_integrate_fraction_3nd = guild.integrate_fraction(bob)
    # alice get same rewards with bob
    alice_reward_2 = alice_integrate_fraction_3nd - alice_integrate_fraction_2nd
    bob_reward_2 = bob_integrate_fraction_3nd - bob_integrate_fraction_2nd
    assert approx(alice_reward_2, bob_reward_2, TOL)


def test_kick(chain, accounts, Guild, voting_escrow, token, guild_controller, gas_token, GasEscrow):
    alice, bob, carl, dan = accounts[:4]
    amount_dan = 10000 * 10 ** 18
    # token.transfer(dan, amount_dan, {"from": alice})
    token.approve(voting_escrow.address, amount_dan * 10, {"from": dan})
    # dan create lock can not less than one year
    with brownie.reverts("Voting lock must be 1 year min"):
        voting_escrow.create_lock(amount_dan, chain[-1].timestamp + 12 * WEEK, {"from": dan})

    # alice create lock for dan
    voting_escrow.create_lock_for(dan, amount_dan, chain[-1].timestamp + YEAR + 10000, {"from": alice})
    # alice create guild
    guild = create_guild(chain, guild_controller, gas_token, alice, 0, Guild)

    # Move to timing which is good for testing - beginning of a UTC week
    chain.sleep((chain[-1].timestamp // WEEK + 1) * WEEK - chain[-1].timestamp)
    chain.mine()

    # dan join guild
    guild.join_guild({"from": dan})
    # dan get boost
    gas_amount = 100000 * 10 ** 18
    setup_gas_token(accounts, dan, gas_amount, gas_token, guild_controller, GasEscrow)
    gas_escrow = guild_controller.gas_addr_escrow(gas_token.address)
    gas_contract = GasEscrow.at(gas_escrow)
    chain.sleep(60)
    guild.user_checkpoint(dan, {"from": dan})

    chain.sleep(10 * WEEK)
    with brownie.reverts("dev: kick not allowed"):
        guild.kick(dan, {"from": alice})

    # bob join guild and leave guild for checkpoint guild.
    guild.join_guild({"from": bob})
    chain.sleep(YEAR // 2)
    guild.leave_guild({"from": bob})
    chain.sleep(YEAR // 2)
    chain.mine()
    guild.kick(dan, {"from": alice})
    # record dan_integrate_fraction
    dan_integrate_fraction = guild.integrate_fraction(dan)
    chain.sleep(WEEK)
    chain.mine()
    guild.user_checkpoint(dan, {"from": dan})
    # dan has no working balance, no rewards any more
    assert dan_integrate_fraction == guild.integrate_fraction(dan)

    # dan create lock again with 4 yrs
    voting_escrow.withdraw({"from": dan})
    voting_escrow.create_lock(amount_dan, chain[-1].timestamp + MAXTIME, {"from": dan})
    guild.user_checkpoint(dan, {"from": dan})

    # bob join guild
    guild.join_guild({"from": bob})
    # bob update balance for checkpoint guild.
    for _ in range(6):
        chain.sleep(YEAR // 2)
        chain.mine()
        voting_escrow.increase_unlock_time(voting_escrow.locked(bob)["end"] + YEAR // 2, {"from": bob})
        guild.user_checkpoint(bob, {"from": bob})

    assert gas_contract.balanceOf(dan) == 0
    guild.kick(dan, {"from": alice})
    assert approx(guild.working_balances(dan), voting_escrow.balanceOf(dan) * 0.4, 1e-15)
    chain.sleep(WEEK)
    chain.mine()
    with brownie.reverts("dev: kick not needed"):
        guild.kick(dan, {"from": alice})


def test_gas_escrow_create_end(chain, accounts, Guild, voting_escrow, token, guild_controller, gas_token, GasEscrow):
    alice, bob = accounts[:2]

    # alice create guild
    guild = create_guild(chain, guild_controller, gas_token, alice, 0, Guild)

    # Move to timing which is good for testing - beginning of a UTC week
    chain.sleep((chain[-1].timestamp // WEEK + 1) * WEEK - chain[-1].timestamp)
    chain.mine()

    gas_escrow = guild_controller.gas_addr_escrow(gas_token.address)
    gas_contract = GasEscrow.at(gas_escrow)

    gas_amount = 100000 * 10 ** 18
    # alice get boost
    setup_gas_token(accounts, alice, gas_amount, gas_token, guild_controller, GasEscrow)
    guild.user_checkpoint(alice, {"from": alice})
    # advance 4 yrs
    for i in range(8):
        chain.sleep(YEAR // 2)
        chain.mine()
        voting_escrow.increase_unlock_time(voting_escrow.locked(alice)["end"] + YEAR // 2, {"from": alice})
        increase_gas = 10 * 10 ** 18
        if i % 2 == 0:
            gas_contract.increase_amount(increase_gas, {"from": alice})
        guild.user_checkpoint(alice, {"from": alice})

    # check gas balance to 0
    assert gas_contract.balanceOf(alice) == 0

    # clear gas
    gas_contract.clear_gas({"from": alice})
    chain.sleep(100)
    chain.mine()
    # can create gas again
    setup_gas_token(accounts, alice, gas_amount, gas_token, guild_controller, GasEscrow)


def test_vote_escrow_create_for(chain, accounts, Guild, voting_escrow, token, guild_controller, gas_token, GasEscrow):
    alice, bob, carl, dan = accounts[:4]
    amount_dan = 10000 * 10 ** 18
    # token.transfer(dan, amount_dan, {"from": alice})
    token.approve(voting_escrow.address, amount_dan * 10, {"from": dan})
    # dan create lock can not less than one year

    voting_escrow.create_lock_for(dan, amount_dan, chain[-1].timestamp + YEAR + 10000, {"from": alice})

    with brownie.reverts("Can not create lock for contract"):
        voting_escrow.create_lock_for(token, amount_dan, chain[-1].timestamp + YEAR + 10000, {"from": alice})

    with brownie.reverts("dev: use create lock"):
        voting_escrow.create_lock_for(alice, amount_dan, chain[-1].timestamp + YEAR + 10000, {"from": alice})


def setup_gas_token(accounts, account, gas_amount, gas_token, guild_controller, GasEscrow):
    # deposit game token to gas escrow and boost
    gas_token.transfer(account, gas_amount, {"from": accounts[0]})
    gas_escrow_addr = guild_controller.gas_addr_escrow(gas_token.address)
    gas_token.approve(gas_escrow_addr, gas_amount * 10, {"from": account})
    gas_escrow_contract = GasEscrow.at(gas_escrow_addr)
    gas_escrow_contract.create_gas(gas_amount, {"from": account})


def create_guild(chain, guild_controller, gas_token, guild_owner, commission_rate, Guild):
    guild_type = 0
    type_weight = 1 * 10 ** 18
    guild_controller.add_type("Gas MOH", "GASMOH", gas_token.address, type_weight)
    chain.sleep(H)
    guild_controller.create_guild(guild_owner, guild_type, commission_rate, {"from": guild_owner})
    guild_address = guild_controller.guild_owner_list(guild_owner)
    guild_contract = Guild.at(guild_address)
    guild_contract.user_checkpoint(guild_owner, {"from": guild_owner})
    return guild_contract
