package com.github.miyako.sonar.fourd;

import org.sonar.api.Plugin;
import org.sonar.api.config.PropertyDefinition;
import org.sonar.api.config.PropertyDefinition.ConfigScope;

public final class FourDPlugin implements Plugin {
  @Override
  public void define(Context context) {
    context.addExtensions(
        FourDLanguage.class,
        PropertyDefinition.builder(FourDLanguage.FILE_SUFFIXES_KEY)
            .name("File suffixes")
            .description("File suffixes identifying 4D source files, without the leading dot.")
            .category("4D")
            .subCategory("General")
            .defaultValue(FourDLanguage.DEFAULT_FILE_SUFFIX)
            .multiValues(true)
            .onConfigScopes(ConfigScope.PROJECT)
            .build());
  }
}
