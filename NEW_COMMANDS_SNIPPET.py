# ===========================================================================
# CODE MẪU ĐỂ DÁN VÀO discord_bot.py - xem HUONG_DAN_TICH_HOP.md để biết
# chính xác cần thêm gì ở đâu. File này KHÔNG được bot import trực tiếp.
# ===========================================================================

# --- 1. Thêm vào phần import ở đầu discord_bot.py (cạnh "import features") ---
#
# import pets
# import pvp
# import season
# import business_v2


# --- 2. Lệnh /pet: xem, nhận nuôi, cho ăn, chơi, train ---

@tree.command(name="pet", description="Xem, nhận nuôi, cho ăn hoặc train thú cưng của bạn")
async def pet_cmd(interaction: discord.Interaction):
    await pets.check_starvation(interaction.user.id)
    pet = await pets.get_pet_live(interaction.user.id)
    if not pet:
        container = features.build_container(
            title="🐾 Bạn chưa có thú cưng nào",
            description=(
                f"Nhận nuôi 1 pet với giá **{pets.ADOPT_COST} MICK**!\n\n"
                + "\n".join(f"{v['emoji']} **{v['name']}** — {v['flavor']}" for v in pets.SPECIES.values())
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(view=AdoptPetView(interaction.user.id, container))
        return
    container = pets.build_pet_container(interaction.user.display_name, pet)
    await interaction.response.send_message(view=PetActionView(interaction.user.id, container))


class AdoptSpeciesModal(discord.ui.Modal, title="Đặt tên cho pet"):
    ten = discord.ui.TextInput(label="Tên pet", max_length=24, placeholder="vd: Mít")

    def __init__(self, user_id: int, species: str):
        super().__init__()
        self.user_id = user_id
        self.species = species

    async def on_submit(self, interaction: discord.Interaction):
        result = await pets.adopt_pet(self.user_id, self.species, str(self.ten))
        if not result["ok"]:
            reason_map = {
                "already_has_pet": "❌ Bạn đã có 1 pet rồi.",
                "insufficient_funds": f"❌ Bạn không đủ {pets.ADOPT_COST} MICK để nhận nuôi.",
                "invalid_species": "❌ Loài không hợp lệ.",
            }
            await interaction.response.send_message(reason_map.get(result["reason"], "❌ Có lỗi xảy ra."), ephemeral=True)
            return
        container = pets.build_pet_container(interaction.user.display_name, result["pet"])
        await interaction.response.send_message(
            content="🎉 Chúc mừng bạn đã nhận nuôi 1 thú cưng mới!",
            view=PetActionView(self.user_id, container),
        )


class AdoptPetView(discord.ui.LayoutView):
    def __init__(self, user_id: int, container: discord.ui.Container):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.add_item(container)
        row = discord.ui.ActionRow()
        self.add_item(row)
        for key, info in pets.SPECIES.items():
            btn = discord.ui.Button(label=info["name"], emoji=info["emoji"], style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(key)
            row.add_item(btn)

    def _make_cb(self, species: str):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("❌ Không phải pet của bạn.", ephemeral=True)
                return
            await interaction.response.send_modal(AdoptSpeciesModal(self.user_id, species))
        return _cb


class PetActionView(discord.ui.LayoutView):
    def __init__(self, user_id: int, container: discord.ui.Container):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.add_item(container)
        row = discord.ui.ActionRow()
        self.add_item(row)

        feed_btn = discord.ui.Button(label="Cho ăn", emoji="🍖", style=discord.ButtonStyle.success)
        feed_btn.callback = self._feed
        row.add_item(feed_btn)

        play_btn = discord.ui.Button(label="Chơi cùng", emoji="🎾", style=discord.ButtonStyle.primary)
        play_btn.callback = self._play
        row.add_item(play_btn)

        train_btn = discord.ui.Button(label="Train", emoji="🏋️", style=discord.ButtonStyle.secondary)
        train_btn.callback = self._train
        row.add_item(train_btn)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Không phải pet của bạn.", ephemeral=True)
            return False
        return True

    async def _feed(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        result = await pets.feed_pet(self.user_id)
        if not result["ok"]:
            reason_map = {
                "ran_away": "😭 Pet của bạn đã bỏ trốn vì bị bỏ đói quá lâu...",
                "no_pet": "❌ Bạn chưa có pet.",
                "insufficient_funds": f"❌ Cần {pets.FEED_COST} MICK để cho ăn.",
            }
            await interaction.response.send_message(reason_map.get(result["reason"], "❌ Lỗi."), ephemeral=True)
            return
        container = pets.build_pet_container(interaction.user.display_name, result["pet"])
        await interaction.response.edit_message(view=PetActionView(self.user_id, container))

    async def _play(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        result = await pets.play_with_pet(self.user_id)
        if not result["ok"]:
            if result["reason"] == "cooldown":
                mins = result["remaining"] // 60
                await interaction.response.send_message(f"⏳ Pet cần nghỉ, thử lại sau {mins} phút nữa.", ephemeral=True)
            elif result["reason"] == "ran_away":
                await interaction.response.send_message("😭 Pet của bạn đã bỏ trốn vì bị bỏ đói quá lâu...", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Bạn chưa có pet.", ephemeral=True)
            return
        container = pets.build_pet_container(interaction.user.display_name, result["pet"])
        await interaction.response.edit_message(view=PetActionView(self.user_id, container))

    async def _train(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        result = await pets.train_pet(self.user_id)
        if not result["ok"]:
            reason_map = {
                "ran_away": "😭 Pet của bạn đã bỏ trốn vì bị bỏ đói quá lâu...",
                "no_pet": "❌ Bạn chưa có pet.",
                "too_hungry": "❌ Pet quá đói để train, cho ăn trước đã!",
                "insufficient_funds": f"❌ Cần {pets.TRAIN_MICK_COST} MICK để train.",
            }
            if result["reason"] == "cooldown":
                mins = result["remaining"] // 60
                await interaction.response.send_message(f"⏳ Pet cần nghỉ, thử lại sau {mins} phút nữa.", ephemeral=True)
                return
            await interaction.response.send_message(reason_map.get(result["reason"], "❌ Lỗi."), ephemeral=True)
            return
        container = pets.build_pet_container(interaction.user.display_name, result["pet"])
        extra = f"\n\n✨ +{result['gained_xp']} XP" + (" · 🎉 LÊN CẤP!" if result["leveled_up"] else "")
        await interaction.response.edit_message(view=PetActionView(self.user_id, container))
        await interaction.followup.send(extra, ephemeral=True)


# --- 3. Lệnh /pvp: thách đấu người khác ---

@tree.command(name="pvp", description="Thách đấu 1v1 cược MICK với người khác (Oẳn tù tì / Tài xỉu đối đầu)")
@discord.app_commands.describe(doi_thu="Người bạn muốn thách đấu", che_do="Chế độ đấu", cuoc="Số MICK cược mỗi bên")
@discord.app_commands.choices(che_do=[
    discord.app_commands.Choice(name="Oẳn Tù Tì", value="rps"),
    discord.app_commands.Choice(name="Tài Xỉu Đối Đầu", value="taixiu"),
])
async def pvp_cmd(interaction: discord.Interaction, doi_thu: discord.Member, che_do: discord.app_commands.Choice[str], cuoc: int):
    if doi_thu.bot:
        await interaction.response.send_message("❌ Không thể thách đấu bot.", ephemeral=True)
        return
    result = await pvp.create_challenge(interaction.user.id, doi_thu.id, che_do.value, cuoc)
    if not result["ok"]:
        reason_map = {
            "self_challenge": "❌ Không thể tự thách đấu chính mình.",
            "invalid_amount": "❌ Số MICK cược không hợp lệ.",
            "insufficient_funds": "❌ Bạn không đủ MICK để cược mức này.",
        }
        await interaction.response.send_message(reason_map.get(result["reason"], "❌ Lỗi."), ephemeral=True)
        return
    view = pvp.ChallengeInviteView(
        result["challenge_id"], interaction.user.id, doi_thu.id,
        interaction.user.display_name, doi_thu.display_name, che_do.value, cuoc,
    )
    await interaction.response.send_message(content=doi_thu.mention, view=view)


# --- 4. Lệnh /season: xem bảng xếp hạng mùa hiện tại ---

@tree.command(name="season", description="Xem bảng xếp hạng mùa giải hiện tại (reset mỗi tháng, có thưởng top 3)")
async def season_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    rollover = await season.maybe_rollover_season()
    if rollover and rollover["rewards"]:
        lines = []
        for r in rollover["rewards"]:
            member = interaction.guild.get_member(r["user_id"]) if interaction.guild else None
            name = member.display_name if member else f"User {r['user_id']}"
            lines.append(f"🏆 Hạng {r['rank']}: **{name}** nhận **{r['reward']} MICK**")
        await interaction.followup.send(
            f"📅 Mùa **{rollover['old_season_id']}** đã kết thúc! Trao thưởng:\n" + "\n".join(lines)
        )

    state = await season.get_season_state()
    entries = await season.get_season_leaderboard(state["season_id"])

    def _name_lookup(uid: int) -> str:
        member = interaction.guild.get_member(uid) if interaction.guild else None
        return member.display_name if member else f"User {uid}"

    container = season.build_leaderboard_container(state["season_id"], entries, _name_lookup)
    await interaction.followup.send(view=features.SimpleContainerLayout(container))


# --- 5. Lệnh /market: xem thị trường kinh doanh hiện tại (dùng với business_v2) ---

@tree.command(name="market", description="Xem hệ số thị trường hiện tại của các loại hình kinh doanh")
async def market_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    multipliers = await business_v2.get_all_market_multipliers()
    container = business_v2.build_market_container(interaction.user.display_name, multipliers)
    await interaction.followup.send(view=features.SimpleContainerLayout(container))


# --- 6. Thêm nút "Nâng cấp" vào BusinessView hiện có (trong class BusinessView) ---
#
# Trong __init__ của BusinessView (discord_bot.py, khoảng dòng 3021), sau khi
# tạo các nút hiện có, thêm 1 nút mới gọi business_v2.upgrade_business():
#
#     upgrade_btn = discord.ui.Button(label="Nâng cấp", emoji="⬆️", style=discord.ButtonStyle.success)
#     upgrade_btn.callback = self._on_upgrade
#     row.add_item(upgrade_btn)
#
# Và thêm callback (cần biết kind đang chọn, dùng self.selected_kind nếu đã
# có select box tương tự pattern cũ):
#
#     async def _on_upgrade(self, interaction: discord.Interaction):
#         result = await business_v2.upgrade_business(self.owner_id, self.selected_kind)
#         if not result["ok"]:
#             reason_map = {
#                 "not_opened": "❌ Bạn chưa mở cơ sở này.",
#                 "max_level": "❌ Cơ sở đã đạt cấp tối đa (5).",
#                 "insufficient_funds": f"❌ Cần {result.get('cost')} MICK để nâng cấp.",
#             }
#             await interaction.response.send_message(reason_map.get(result["reason"], "❌ Lỗi."), ephemeral=True)
#             return
#         await interaction.response.send_message(
#             f"✅ Đã nâng cấp lên level {result['level']}! (+{business_v2.UPGRADE_INCOME_BONUS_PER_LEVEL*100:.0f}% thu nhập/level)",
#             ephemeral=True,
#         )
#         summary = await features.get_summary(self.owner_id)
#         container = features.build_summary_container(interaction.user.display_name, summary)
#         await interaction.edit_original_response(view=BusinessView(self.owner_id, container))


# --- 7. Thay thế lời gọi tick trong business_tick_loop (khoảng dòng 428) ---
#
# TRƯỚC:
#     @tasks.loop(seconds=BUSINESS_TICK_SEC)
#     async def business_tick_loop():
#         await features.run_income_tick()
#
# SAU:
#     @tasks.loop(seconds=BUSINESS_TICK_SEC)
#     async def business_tick_loop():
#         await business_v2.run_income_tick_v2()


# --- 8. Cộng điểm mùa mỗi khi user kiếm MICK ở các nơi khác (tuỳ chọn) ---
#
# Ở bất kỳ chỗ nào trong discord_bot.py gọi economy.add_mick(user_id, amount)
# với amount dương (thắng game, level up, PvP, Daily...), có thể gọi thêm:
#
#     await season.add_season_score(user_id, amount)
#
# Không bắt buộc ở MỌI chỗ - chỉ cần ở những nguồn thu nhập chính là đủ để
# bảng xếp hạng mùa có ý nghĩa (business_v2.run_income_tick_v2 đã tự làm
# việc này rồi, không cần thêm lại).


# --- 9. Thêm vào /help (HelpLayoutView) một mục mới liệt kê lệnh mới ---
#
# "🐾 Thú cưng & PvP": "/pet, /pvp, /season, /market"
