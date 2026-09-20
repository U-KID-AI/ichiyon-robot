-- Fixed avatar vocabulary; preserves all existing queue command types.
DO $$
BEGIN
    ALTER TABLE minecraft_command_queue
        DROP CONSTRAINT IF EXISTS minecraft_command_queue_type_safe;

    ALTER TABLE minecraft_command_queue
        ADD CONSTRAINT minecraft_command_queue_type_safe
        CHECK (
            command_type IN (
                'avatar_kiana_spawn_near_player',
                'avatar_kiana_remove_near_player',
                'avatar_mei_spawn_near_player',
                'avatar_mei_remove_near_player',
                'avatar_bronya_spawn_near_player',
                'avatar_bronya_remove_near_player',
                'avatar_albert_spawn_near_player',
                'avatar_albert_remove_near_player',
                'avatar_all_remove_near_player',
                'narita_carpet',
                'structure_block',
                'command_block',
                'barrier_block',
                'light_block',
                'jigsaw_block',
                'structure_void',
                'repeating_command_block',
                'chain_command_block',
                'taketumi_spawn_egg',
                'e_schrift_item',
                'held_item_inspect',
                'sync_diagnostics',
                'join_history',
                'server_status',
                'taketumi_spawn_near_player',
                'taketumi_remove_near_player',
                'poster_irsia',
                'poster_raio',
                'poster_trent',
                'poster_aurelia',
                'poster_killzael',
                'poster_caravan_mammoth',
                'poster_itsutake',
                'poster_cat_tuner',
                'poster_wilbert',
                'poster_miltio',
                'poster_ace',
                'poster_eyes_eden',
                'poster_akuki',
                'softshell_crab',
                'gonta_spawn_near_player',
                'gonta_remove_near_player',
                'molcar_spawn_near_player',
                'molcar_remove_near_player',
                'molcar3_spawn_near_player',
                'molcar3_remove_near_player'
            )
        );
END
$$;
