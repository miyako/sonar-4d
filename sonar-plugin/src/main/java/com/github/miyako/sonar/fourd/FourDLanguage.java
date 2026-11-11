package com.github.miyako.sonar.fourd;

import java.util.Arrays;
import org.sonar.api.config.Configuration;
import org.sonar.api.resources.AbstractLanguage;

public final class FourDLanguage extends AbstractLanguage {
  public static final String KEY = "fourd";
  public static final String NAME = "4D";
  public static final String FILE_SUFFIXES_KEY = "sonar.fourd.file.suffixes";
  public static final String DEFAULT_FILE_SUFFIX = "4dm";

  private final Configuration configuration;

  public FourDLanguage(Configuration configuration) {
    super(KEY, NAME);
    this.configuration = configuration;
  }

  @Override
  public String[] getFileSuffixes() {
    String[] suffixes = Arrays.stream(configuration.getStringArray(FILE_SUFFIXES_KEY))
        .map(String::trim)
        .filter(suffix -> !suffix.isEmpty())
        .toArray(String[]::new);
    return suffixes.length == 0 ? new String[] {DEFAULT_FILE_SUFFIX} : suffixes;
  }
}
